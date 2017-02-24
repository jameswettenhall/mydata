"""
The main controller class for managing datafile verifications.

class VerifyDatafileRunnable(object):
  Run:
    HandleNonExistentDataFile:
      Post DidntFindDatafileOnServerEvent  # DataFile record doesn't exist
    HandleExistingDatafile:
      HandleExistingVerifiedDatafile:
        Post FoundVerifiedDatafileEvent  # Verified DFO exists!
      HandleExistingUnverifiedDatafile:
        HandleResumableUpload:
          HandleFullSizeResumableUpload:
            Post FoundUnverifiedDatafileEvent
          HandleIncompleteResumableUpload:
            Post UnverifiedDatafileOnServerEvent
        HandleUnresumableUpload:
          Post FoundUnverifiedDatafileEvent
"""

import os
import threading
import traceback

import wx

from mydata.models.settings.miscellaneous import MiscellaneousSettingsModel
from mydata.models.replica import ReplicaModel
from mydata.models.verification import VerificationModel
from mydata.models.verification import VerificationStatus
from mydata.models.datafile import DataFileModel
from mydata.controllers.uploads import UploadMethod
from mydata.utils.exceptions import DoesNotExist
from mydata.utils.exceptions import MissingMyDataReplicaApiEndpoint
from mydata.events import PostEvent
from mydata.logs import logger


class VerifyDatafileRunnable(object):
    """
    The Run method of this class provides the functionality of
    the verification workers.  Data files found locally are
    looked up on the MyTardis server, and are classified according
    to whether they are found on the server, whether they are
    verified, and if not, whether they have been completely or
    partially uploaded.
    """
    # pylint: disable=too-many-instance-attributes
    def __init__(self, foldersController, foldersModel, folderModel,
                 dataFileIndex, settingsModel, testRun=False):
        self.foldersController = foldersController
        self.foldersModel = foldersModel
        self.folderModel = folderModel
        self.dataFileIndex = dataFileIndex
        self.settingsModel = settingsModel
        self.verificationsModel = foldersController.verificationsModel
        self.verificationModel = None
        self.testRun = testRun

    def GetDatafileIndex(self):
        """
        Return the DataFile index within the folderModel.
        """
        return self.dataFileIndex

    def GetDatafilePath(self):
        """
        Return the path to the DataFile.
        """
        return self.folderModel.GetDataFilePath(self.dataFileIndex)

    def GetDatafileSize(self):
        """
        Return the size of the DataFile.
        """
        return self.folderModel.GetDataFileSize(self.dataFileIndex)

    def Run(self):
        """
        This method provides the functionality of
        the verification workers.  Data files found locally are
        looked up on the MyTardis server, and are classified according
        to whether they are found on the server, whether they are
        verified, and if not, whether they have been completely or
        partially uploaded.
        """
        dataFilePath = self.folderModel.GetDataFilePath(self.dataFileIndex)
        dataFileDirectory = \
            self.folderModel.GetDataFileDirectory(self.dataFileIndex)
        dataFileName = os.path.basename(dataFilePath)
        fc = self.foldersController  # pylint: disable=invalid-name
        if not hasattr(fc, "verificationsThreadingLock"):
            fc.verificationsThreadingLock = threading.Lock()
        fc.verificationsThreadingLock.acquire()
        try:
            verificationDataViewId = self.verificationsModel.GetMaxDataViewId() + 1
            self.verificationModel = \
                VerificationModel(dataViewId=verificationDataViewId,
                                  folderModel=self.folderModel,
                                  dataFileIndex=self.dataFileIndex)
            self.verificationsModel.AddRow(self.verificationModel)
        finally:
            fc.verificationsThreadingLock.release()
        self.verificationModel.SetMessage("Looking for matching file on "
                                          "MyTardis server...")
        self.verificationModel.SetStatus(VerificationStatus.IN_PROGRESS)
        self.verificationsModel.MessageUpdated(self.verificationModel)

        try:
            dataset = self.folderModel.GetDatasetModel()
            if not dataset:  # test runs don't create required datasets
                raise DoesNotExist("Dataset doesn't exist.")
            existingDatafile = DataFileModel.GetDataFile(
                settingsModel=self.settingsModel,
                dataset=dataset,
                filename=dataFileName,
                directory=dataFileDirectory)
            self.verificationModel.SetMessage("Found datafile on "
                                              "MyTardis server.")
            self.verificationModel.SetStatus(VerificationStatus.FOUND_VERIFIED)
            self.verificationsModel.MessageUpdated(self.verificationModel)
            self.HandleExistingDatafile(existingDatafile)
        except DoesNotExist:
            self.HandleNonExistentDataFile()
        except:
            logger.error(traceback.format_exc())

    def HandleNonExistentDataFile(self):
        """
        If file doesn't exist on the server, it needs to be uploaded.
        """
        self.verificationModel.SetMessage("Didn't find datafile on "
                                          "MyTardis server.")
        self.verificationModel.SetStatus(VerificationStatus.NOT_FOUND)
        self.verificationsModel.MessageUpdated(self.verificationModel)
        self.verificationsModel.SetComplete(self.verificationModel)
        event = self.foldersController.DidntFindDatafileOnServerEvent(
            foldersController=self.foldersController,
            folderModel=self.folderModel,
            dataFileIndex=self.dataFileIndex,
            verificationModel=self.verificationModel)
        PostEvent(event)

    def HandleExistingDatafile(self, existingDatafile):
        """
        Check if existing DataFile is verified.
        """
        replicas = existingDatafile.GetReplicas()
        if len(replicas) == 0 or not replicas[0].IsVerified():
            self.HandleExistingUnverifiedDatafile(existingDatafile)
        else:
            self.HandleExistingVerifiedDatafile()

    def HandleExistingUnverifiedDatafile(self, existingDatafile):
        """
        If the existing unverified DataFile was uploaded via POST, we just
        need to wait for it to be verified.  But if it was uploaded via
        staging, we might be able to resume a partial upload.
        """
        self.verificationModel.SetExistingUnverifiedDatafile(existingDatafile)
        dataFilePath = self.folderModel.GetDataFilePath(self.dataFileIndex)
        replicas = existingDatafile.GetReplicas()
        message = "Found datafile record for %s " \
            "but it has no verified replicas." % dataFilePath
        logger.debug(message)
        message = "Found unverified datafile record on MyTardis."
        self.verificationModel.SetMessage(message)
        uploadToStagingRequest = self.settingsModel.uploadToStagingRequest
        if self.foldersController.uploadMethod == \
                UploadMethod.VIA_STAGING and \
                uploadToStagingRequest is not None and \
                uploadToStagingRequest.IsApproved() and \
                len(replicas) > 0:
            # Can resume partial uploads:
            self.HandleResumableUpload(existingDatafile)
        else:
            # Can't resume partial uploads:
            self.HandleUnresumableUpload(existingDatafile)

    def HandleResumableUpload(self, existingDatafile):
        """
        Determine whether part of the file is already available on staging.

        The name of this method comes from MyData v0.6.x and earlier which
        uploaded files in chunks, so it could resume partial uploads by
        counting chunks in partial uploads.  Chunking has been removed in
        v0.7.0.  This method is now used when we are using the STAGING
        upload method and we found an existing DataFileObject, so resuming
        means checking if the previous upload can be found on the staging
        server and whether it is the correct size.

        MyData uses the /api/v1/mydata_replica/ API endpoint
        on the MyTardis server, which is provided by the
        mytardis-app-mydata app.
        """
        replicas = existingDatafile.GetReplicas()
        try:
            bytesUploadedPreviously = \
                ReplicaModel.CountBytesUploadedToStaging(
                    self.settingsModel, replicas[0].GetId())
            logger.debug("%s bytes uploaded to staging for %s"
                         % (bytesUploadedPreviously,
                            replicas[0].GetUri()))
        except MissingMyDataReplicaApiEndpoint:
            message = (
                "Please ask your MyTardis administrator to "
                "upgrade the mytardis-app-mydata app to include "
                "the /api/v1/mydata_replica/ API endpoint.")
            PostEvent(
                self.foldersController
                .ShowMessageDialogEvent(title="MyData",
                                        message=message,
                                        icon=wx.ICON_ERROR))
            return
        if bytesUploadedPreviously == int(existingDatafile.GetSize()):
            self.HandleFullSizeResumableUpload(existingDatafile)
        else:
            self.HandleIncompleteResumableUpload(
                existingDatafile,
                bytesUploadedPreviously)

    def HandleFullSizeResumableUpload(self, existingDatafile):
        """
        If the existing unverified DataFile upload is the correct size
        in staging, then we can request its verification, but no upload
        is needed.
        """
        dataFilePath = self.folderModel.GetDataFilePath(self.dataFileIndex)
        self.verificationModel\
            .SetMessage("Found unverified full-size datafile "
                        "on staging server.")
        self.verificationModel.SetStatus(
            VerificationStatus.FOUND_UNVERIFIED_FULL_SIZE)
        self.verificationsModel.MessageUpdated(self.verificationModel)
        self.folderModel.SetDataFileUploaded(self.dataFileIndex, True)
        self.foldersModel.FolderStatusUpdated(self.folderModel)
        if existingDatafile and not self.testRun:
            if existingDatafile.GetMd5Sum() == \
                    MiscellaneousSettingsModel.GetFakeMd5Sum():
                logger.warning("MD5(%s): %s" %
                               (dataFilePath, existingDatafile.GetMd5Sum()))
            else:
                DataFileModel.Verify(self.settingsModel, existingDatafile.GetId())
        self.verificationsModel.SetComplete(self.verificationModel)
        PostEvent(
            self.foldersController
            .FoundUnverifiedDatafileEvent(
                folderModel=self.folderModel,
                dataFileIndex=self.dataFileIndex,
                dataFilePath=dataFilePath))
        if self.testRun:
            message = "FOUND UNVERIFIED UPLOAD FOR: %s" \
                % self.folderModel.GetDataFileRelPath(self.dataFileIndex)
            logger.testrun(message)

    def HandleIncompleteResumableUpload(self, existingDatafile,
                                        bytesUploadedPreviously):
        """
        Resume partial upload.
        """
        dataFilePath = self.folderModel.GetDataFilePath(self.dataFileIndex)
        self.verificationModel\
            .SetMessage("Found partially uploaded datafile "
                        "on staging server.")
        self.verificationModel\
            .SetStatus(VerificationStatus
                       .FOUND_UNVERIFIED_NOT_FULL_SIZE)
        self.verificationsModel.MessageUpdated(self.verificationModel)
        logger.debug("Re-uploading \"%s\" to staging, because "
                     "the file size is %s bytes in staging, "
                     "but it should be %s bytes."
                     % (dataFilePath,
                        bytesUploadedPreviously,
                        existingDatafile.GetSize()))
        self.verificationsModel.SetComplete(self.verificationModel)
        PostEvent(
            self.foldersController.UnverifiedDatafileOnServerEvent(
                foldersController=self.foldersController,
                folderModel=self.folderModel,
                dataFileIndex=self.dataFileIndex,
                existingUnverifiedDatafile=existingDatafile,
                bytesUploadedPreviously=bytesUploadedPreviously,
                verificationModel=self.verificationModel))

    def HandleUnresumableUpload(self, existingDatafile):
        """
        We found an unverified datafile on the server for which
        there is no point in checking for a resumable partial
        upload.

        This is usually because we are uploading using the POST upload method.
        Or we could be using the STAGING method but failed to find any
        DataFileObjects on the server for the datafile.
        """
        dataFilePath = self.folderModel.GetDataFilePath(self.dataFileIndex)
        logger.debug("Found unverified datafile record for \"%s\" "
                     "on MyTardis." % dataFilePath)
        self.verificationModel.SetMessage("Found unverified datafile record.")
        self.folderModel.SetDataFileUploaded(self.dataFileIndex, True)
        self.foldersModel.FolderStatusUpdated(self.folderModel)
        if self.foldersController.uploadMethod == UploadMethod.HTTP_POST:
            self.verificationModel.SetStatus(
                VerificationStatus.FOUND_UNVERIFIED_FULL_SIZE)
        else:
            self.verificationModel.SetStatus(
                VerificationStatus.FOUND_UNVERIFIED_NO_DFOS)
        self.verificationsModel.MessageUpdated(self.verificationModel)
        if existingDatafile and not self.testRun:
            if existingDatafile.GetMd5Sum() == \
                    MiscellaneousSettingsModel.GetFakeMd5Sum():
                logger.warning("MD5(%s): %s" %
                               (dataFilePath, existingDatafile.GetMd5Sum()))
            else:
                DataFileModel.Verify(self.settingsModel, existingDatafile.GetId())
        self.verificationsModel.SetComplete(self.verificationModel)
        PostEvent(
            self.foldersController
            .FoundUnverifiedDatafileEvent(
                folderModel=self.folderModel,
                dataFileIndex=self.dataFileIndex,
                dataFilePath=dataFilePath))
        if self.testRun:
            message = "FOUND UNVERIFIED UPLOAD FOR: %s" \
                % self.folderModel.GetDataFileRelPath(self.dataFileIndex)
            logger.testrun(message)

    def HandleExistingVerifiedDatafile(self):
        """
        Found existing verified file on server.
        """
        dataFilePath = self.folderModel.GetDataFilePath(self.dataFileIndex)
        self.folderModel.SetDataFileUploaded(self.dataFileIndex, True)
        self.foldersModel.FolderStatusUpdated(self.folderModel)
        self.verificationsModel.SetComplete(self.verificationModel)
        PostEvent(
            self.foldersController.FoundVerifiedDatafileEvent(
                folderModel=self.folderModel,
                dataFileIndex=self.dataFileIndex,
                dataFilePath=dataFilePath))
        if self.testRun:
            message = "FOUND VERIFIED UPLOAD FOR: %s" \
                % self.folderModel.GetDataFileRelPath(self.dataFileIndex)
            logger.testrun(message)
