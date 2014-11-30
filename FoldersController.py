import os
import sys
import threading
import urllib
import urllib2
import requests
import json
import Queue
import io
import traceback
from datetime import datetime
import mimetypes
import time
import subprocess
import hashlib
import poster

import OpenSSH

from ExperimentModel import ExperimentModel
from DatasetModel import DatasetModel
from UserModel import UserModel
from UploadModel import UploadModel
from UploadModel import UploadStatus
from FolderModel import FolderModel
from FoldersModel import GetFolderTypes
from DataFileModel import DataFileModel
from Exceptions import DoesNotExist
from Exceptions import MultipleObjectsReturned
from Exceptions import Unauthorized
from Exceptions import InternalServerError

from logger.Logger import logger

import wx
import wx.lib.newevent
import wx.dataview

from DragAndDrop import MyFolderDropTarget
from AddFolderDialog import AddFolderDialog


class ConnectionStatus():
    CONNECTED = 0
    DISCONNECTED = 1


class UploadMethod():
    HTTP_POST = 0
    CAT_SSH = 1


class FoldersController():
    def __init__(self, notifyWindow, foldersModel, foldersView, usersModel,
                 uploadsModel, settingsModel):
        self.notifyWindow = notifyWindow
        self.foldersModel = foldersModel
        self.foldersView = foldersView
        self.usersModel = usersModel
        self.uploadsModel = uploadsModel
        self.settingsModel = settingsModel

        self.shuttingDown = False
        self.showingMessageDialog = False

        # These will get overwritten in UploadDataThread, but we need
        # to initialize them here, so that ShutDownUploadThreads()
        # can be called.
        self.numVerificationWorkerThreads = 0
        self.verificationWorkerThreads = []
        self.numUploadWorkerThreads = 0
        self.uploadWorkerThreads = []

        self.lastUsedFolderType = None
        self.folderDropTarget = MyFolderDropTarget(self)
        self.foldersView.SetDropTarget(self.folderDropTarget)

        self.foldersView.Bind(wx.EVT_BUTTON, self.OnOpenFolder,
                              self.foldersView.GetOpenFolderButton())
        self.foldersView.GetDataViewControl()\
            .Bind(wx.dataview.EVT_DATAVIEW_ITEM_ACTIVATED, self.OnOpenFolder)

        self.DidntFindMatchingDatafileOnServerEvent, \
            self.EVT_DIDNT_FIND_MATCHING_DATAFILE_ON_SERVER = \
            wx.lib.newevent.NewEvent()
        self.notifyWindow\
            .Bind(self.EVT_DIDNT_FIND_MATCHING_DATAFILE_ON_SERVER,
                  self.UploadDatafile)

        self.UnverifiedDatafileOnServerEvent, \
            self.EVT_UNVERIFIED_DATAFILE_ON_SERVER = \
            wx.lib.newevent.NewEvent()
        self.notifyWindow\
            .Bind(self.EVT_UNVERIFIED_DATAFILE_ON_SERVER,
                  self.UploadDatafile)

        self.ConnectionStatusEvent, \
            self.EVT_CONNECTION_STATUS = wx.lib.newevent.NewEvent()
        self.notifyWindow.Bind(self.EVT_CONNECTION_STATUS,
                               self.UpdateStatusBar)

        self.ShowMessageDialogEvent, \
            self.EVT_SHOW_MESSAGE_DIALOG = wx.lib.newevent.NewEvent()
        self.notifyWindow.Bind(self.EVT_SHOW_MESSAGE_DIALOG,
                               self.ShowMessageDialog)

        self.UploadsCompleteEvent, \
            self.EVT_UPLOADS_COMPLETE = wx.lib.newevent.NewEvent()
        self.notifyWindow.Bind(self.EVT_UPLOADS_COMPLETE,
                               self.UploadsComplete)

        self.AbortUploadsEvent, \
            self.EVT_ABORT_UPLOADS = wx.lib.newevent.NewEvent()
        self.notifyWindow.Bind(self.EVT_ABORT_UPLOADS,
                               self.ShutDownUploadThreads)

    def Canceled(self):
        return self.canceled

    def SetCanceled(self, canceled=True):
        self.canceled = canceled

    def IsShuttingDown(self):
        return self.shuttingDown

    def SetShuttingDown(self, shuttingDown=True):
        self.shuttingDown = shuttingDown

    def IsShowingMessageDialog(self):
        return self.showingMessageDialog

    def SetShowingMessageDialog(self, showingMessageDialog=True):
        self.showingMessageDialog = showingMessageDialog

    def UpdateStatusBar(self, event):
        if event.connectionStatus == ConnectionStatus.CONNECTED:
            self.notifyWindow.SetConnected(event.myTardisUrl, True)
        else:
            self.notifyWindow.SetConnected(event.myTardisUrl, False)

    def UploadsComplete(self, event):
        if event.success:
            logger.info("Data scan and upload completed successfully.")
        elif event.failed:
            logger.info("Data scan and upload failed.")
        elif event.canceled:
            logger.info("Data scan and upload was canceled.")
        self.notifyWindow.SetOnRefreshRunning(False)

    def ShowMessageDialog(self, event):
        if self.IsShowingMessageDialog():
            return
        self.SetShowingMessageDialog(True)
        dlg = wx.MessageDialog(None, event.message, event.title,
                               wx.OK | event.icon)
        try:
            wx.EndBusyCursor()
            needToRestartBusyCursor = True
        except:
            needToRestartBusyCursor = False
        dlg.ShowModal()
        if needToRestartBusyCursor:
            wx.BeginBusyCursor()
        self.SetShowingMessageDialog(False)
        if hasattr(event, "cb"):
            event.cb()

    def UploadDatafile(self, event):
        """
        This method runs in the main thread, so it shouldn't do anything
        time-consuming or blocking, unless it launches another thread.
        Because this method adds upload tasks to a queue, it is important
        to note that if the queue has a maxsize set, then an attempt to
        add something to the queue could block the GUI thread, making the
        application appear unresponsive.
        """
        if self.IsShuttingDown():
            return
        before = datetime.now()
        folderModel = event.folderModel
        foldersController = event.foldersController
        dfi = event.dataFileIndex
        uploadsModel = foldersController.uploadsModel

        if folderModel not in foldersController.uploadDatafileRunnable:
            foldersController.uploadDatafileRunnable[folderModel] = {}

        uploadDataViewId = uploadsModel.GetMaxDataViewId() + 1
        uploadModel = UploadModel(dataViewId=uploadDataViewId,
                                  folderModel=folderModel,
                                  dataFileIndex=dfi)
        if self.IsShuttingDown():
            return
        uploadsModel.AddRow(uploadModel)
        existingUnverifiedDatafile = False
        if hasattr(event, "existingUnverifiedDatafile"):
            existingUnverifiedDatafile = event.existingUnverifiedDatafile
        foldersController.uploadDatafileRunnable[folderModel][dfi] = \
            UploadDatafileRunnable(self, self.foldersModel, folderModel,
                                   dfi, self.uploadsModel, uploadModel,
                                   self.settingsModel,
                                   existingUnverifiedDatafile)
        if self.IsShuttingDown():
            return
        self.uploadsQueue.put(foldersController
                              .uploadDatafileRunnable[folderModel][dfi])
        after = datetime.now()
        duration = after - before
        if duration.total_seconds() >= 1:
            logger.warning("UploadDatafile for " +
                           folderModel.GetDataFileName(dfi) +
                           " blocked the main GUI thread for %d seconds." +
                           duration.total_seconds())

    def StartDataUploads(self, folderModels=[]):
        class UploadDataThread(threading.Thread):
            def __init__(self, foldersController, foldersModel, settingsModel,
                         folderModels=[]):
                threading.Thread.__init__(self, name="UploadDataThread")
                self.foldersController = foldersController
                self.foldersModel = foldersModel
                self.settingsModel = settingsModel
                self.folderModels = folderModels

                fc = self.foldersController

                fc.canceled = False

                fc.verifyDatafileRunnable = {}
                fc.verificationsQueue = Queue.Queue()
                # FIXME: Number of verify threads should be configurable
                fc.numVerificationWorkerThreads = 25

                fc.verificationWorkerThreads = []
                for i in range(fc.numVerificationWorkerThreads):
                    t = threading.Thread(name="VerificationWorkerThread-%d"
                                         % (i+1),
                                         target=fc.verificationWorker,
                                         args=(i+1,))
                    fc.verificationWorkerThreads.append(t)
                    t.start()

                fc.uploadDatafileRunnable = {}
                fc.uploadsQueue = Queue.Queue()
                # FIXME: Number of upload threads should be configurable
                fc.numUploadWorkerThreads = 5

                uploadToStagingRequest = self.settingsModel\
                    .GetUploadToStagingRequest()
                if uploadToStagingRequest is not None and \
                        uploadToStagingRequest['approved']:
                    logger.info("Uploads to staging have been approved.")
                    fc.uploadMethod = UploadMethod.CAT_SSH
                else:
                    logger.warning("Uploads to staging have not been approved.")
                    message = "Uploads to MyTardis's staging area require " \
                        "approval from your MyTardis administrator.\n\n" \
                        "A request has been sent, and you will be contacted " \
                        "once the request has been approved. Until then, " \
                        "MyData will upload files using HTTP POST, and will " \
                        "only upload one file at a time.\n\n" \
                        "HTTP POST is generally only suitable for small " \
                        "files (up to 100 MB each)."
                    fc.uploadMethodWarningAcknowledged = False
                    def acknowledgedUploadMethodWarning():
                        fc.uploadMethodWarningAcknowledged = True
                    wx.PostEvent(
                        self.foldersController.notifyWindow,
                        self.foldersController
                            .ShowMessageDialogEvent(
                                title="MyData",
                                message=message,
                                icon=wx.ICON_WARNING,
                                cb=acknowledgedUploadMethodWarning))
                    while not fc.uploadMethodWarningAcknowledged:
                        time.sleep(0.1)
                    fc.uploadMethod = UploadMethod.HTTP_POST
                if fc.uploadMethod == UploadMethod.HTTP_POST and \
                        fc.numUploadWorkerThreads > 1:
                    logger.warning(
                        "Using HTTP POST, so setting "
                        "numUploadWorkerThreads to 1, "
                        "because urllib2 is not thread-safe.")
                    fc.numUploadWorkerThreads = 1

                fc.uploadWorkerThreads = []
                for i in range(fc.numUploadWorkerThreads):
                    t = threading.Thread(name="UploadWorkerThread-%d" % (i+1),
                                         target=fc.uploadWorker, args=())
                    fc.uploadWorkerThreads.append(t)
                    t.start()

            def run(self):
                try:
                    if len(folderModels) == 0:
                        # Scan all folders
                        for row in range(0, self.foldersModel.GetRowCount()):
                            if self.foldersController.IsShuttingDown():
                                return
                            folderModel = self.foldersModel.foldersData[row]
                            logger.debug(
                                "UploadDataThread: Starting verifications "
                                "and uploads for folder: " +
                                folderModel.GetFolder())
                            if self.foldersController.IsShuttingDown():
                                return
                            try:
                                # Save MyTardis URL, so if it's changing in the
                                # Settings Dialog while this thread is
                                # attempting to connect, we ensure that any
                                # exception thrown by this thread refers to the
                                # old version of the URL.
                                myTardisUrl = \
                                    self.settingsModel.GetMyTardisUrl()
                                try:
                                    experimentModel = ExperimentModel\
                                        .GetExperimentForFolder(folderModel)
                                except Exception, e:
                                    logger.error(str(e))
                                    wx.PostEvent(
                                        self.foldersController.notifyWindow,
                                        self.foldersController
                                            .ShowMessageDialogEvent(
                                                title="MyData",
                                                message=str(e),
                                                icon=wx.ICON_ERROR))
                                    return
                                folderModel.SetExperiment(experimentModel)
                                CONNECTED = ConnectionStatus.CONNECTED
                                wx.PostEvent(
                                    self.foldersController.notifyWindow,
                                    self.foldersController
                                        .ConnectionStatusEvent(
                                            myTardisUrl=myTardisUrl,
                                            connectionStatus=CONNECTED))
                                try:
                                    datasetModel = DatasetModel\
                                        .CreateDatasetIfNecessary(folderModel)
                                except Exception, e:
                                    logger.error(str(e))
                                    wx.PostEvent(
                                        self.foldersController.notifyWindow,
                                        self.foldersController
                                            .ShowMessageDialogEvent(
                                                title="MyData",
                                                message=str(e),
                                                icon=wx.ICON_ERROR))
                                    return
                                folderModel.SetDatasetModel(datasetModel)
                                self.foldersController\
                                    .VerifyDatafiles(folderModel)
                            except requests.exceptions.ConnectionError, e:
                                if not self.foldersController.IsShuttingDown():
                                    DISCONNECTED = \
                                        ConnectionStatus.DISCONNECTED
                                    wx.PostEvent(
                                        self.foldersController.notifyWindow,
                                        self.foldersController
                                            .ConnectionStatusEvent(
                                                myTardisUrl=myTardisUrl,
                                                connectionStatus=DISCONNECTED))
                                return
                            except ValueError, e:
                                logger.debug("Failed to retrieve experiment "
                                             "for folder " +
                                             str(folderModel.GetFolder()))
                                logger.debug(traceback.format_exc())
                                return
                            if experimentModel is None:
                                logger.debug("Failed to acquire a MyTardis "
                                             "experiment to store data in for"
                                             "folder " +
                                             folderModel.GetFolder())
                                return
                            if self.foldersController.IsShuttingDown():
                                return
                    else:
                        # Scan specific folders (e.g. dragged and dropped),
                        # instead of all of them:
                        for folderModel in folderModels:
                            if self.foldersController.IsShuttingDown():
                                return
                            folderModel.SetCreatedDate()
                            if self.foldersController.IsShuttingDown():
                                return
                            try:
                                # Save MyTardis URL, so if it's changing in the
                                # Settings Dialog while this thread is
                                # attempting to connect, we ensure that any
                                # exception thrown by this thread refers to the
                                # old version of the URL.
                                myTardisUrl = \
                                    self.settingsModel.GetMyTardisUrl()
                                experimentModel = ExperimentModel\
                                    .GetExperimentForFolder(folderModel)
                                folderModel.SetExperiment(experimentModel)
                                CONNECTED = ConnectionStatus.CONNECTED
                                wx.PostEvent(
                                    self.foldersController.notifyWindow,
                                    self.foldersController
                                        .ConnectionStatusEvent(
                                            myTardisUrl=myTardisUrl,
                                            connectionStatus=CONNECTED))
                                datasetModel = DatasetModel\
                                    .CreateDatasetIfNecessary(folderModel)
                                if self.foldersController.IsShuttingDown():
                                    return
                                folderModel.SetDatasetModel(datasetModel)
                                self.foldersController\
                                    .VerifyDatafiles(folderModel)
                            except requests.exceptions.ConnectionError, e:
                                if not self.foldersController.IsShuttingDown():
                                    DISCONNECTED = \
                                        ConnectionStatus.DISCONNECTED
                                    wx.PostEvent(
                                        self.foldersController.notifyWindow,
                                        self.foldersController
                                            .ConnectionStatusEvent(
                                                myTardisUrl=myTardisUrl,
                                                connectionStatus=DISCONNECTED))
                                return
                            except ValueError, e:
                                logger.debug("Failed to retrieve experiment "
                                             "for folder " +
                                             str(folderModel.GetFolder()))
                                return
                            if self.foldersController.IsShuttingDown():
                                return
                except:
                    logger.error(traceback.format_exc())

        self.uploadDataThread = \
            UploadDataThread(foldersController=self,
                             foldersModel=self.foldersModel,
                             settingsModel=self.foldersModel.settingsModel,
                             folderModels=folderModels)
        self.uploadDataThread.start()

    def uploadWorker(self):
        """
        One worker per thread
        By default, up to 5 threads can run simultaneously
        for uploading local data files to
        the MyTardis server.
        """
        while True:
            if self.IsShuttingDown():
                return
            task = self.uploadsQueue.get()
            if task is None:
                wx.PostEvent(
                    self.notifyWindow,
                    self.UploadsCompleteEvent(
                        success=True,
                        failed=False,
                        canceled=self.Canceled()))
                break
            try:
                task.run()
            except ValueError, e:
                if str(e) == "I/O operation on closed file":
                    logger.info(
                        "Ignoring closed file exception - it is normal "
                        "to encounter these exceptions while canceling "
                        "uploads.")
                    self.uploadsQueue.task_done()
                    return
                else:
                    logger.error(traceback.format_exc())
                    self.uploadsQueue.task_done()
                    return
            except:
                logger.error(traceback.format_exc())
                self.uploadsQueue.task_done()
                return

    def verificationWorker(self, verificationWorkerId):
        """
        One worker per thread.
        By default, up to 5 threads can run simultaneously
        for verifying whether local data files exist on
        the MyTardis server.
        """
        while True:
            if self.IsShuttingDown():
                return
            task = self.verificationsQueue.get()
            if task is None:
                break
            try:
                task.run()
            except ValueError, e:
                if str(e) == "I/O operation on closed file":
                    logger.info(
                        "Ignoring closed file exception - it is normal "
                        "to encounter these exceptions while canceling "
                        "uploads.")
                    self.verificationsQueue.task_done()
                    return
                else:
                    logger.error(traceback.format_exc())
                    self.verificationsQueue.task_done()
                    return
            except:
                logger.error(traceback.format_exc())
                self.verificationsQueue.task_done()
                return

    def ShutDownUploadThreads(self, event=None):
        if self.IsShuttingDown():
            return
        self.SetShuttingDown(True)
        logger.debug("Shutting down uploads threads...")
        self.SetCanceled()
        self.uploadsModel.CancelRemaining()
        if hasattr(self, 'uploadDataThread'):
            logger.debug("Joining FoldersController's UploadDataThread...")
            self.uploadDataThread.join()
            logger.debug("Joined FoldersController's UploadDataThread.")
        logger.debug("Shutting down FoldersController upload worker threads.")
        for i in range(self.numUploadWorkerThreads):
            self.uploadsQueue.put(None)
        for t in self.uploadWorkerThreads:
            t.join()
        logger.debug("Shutting down FoldersController verification "
                     "worker threads.")
        for i in range(self.numVerificationWorkerThreads):
            self.verificationsQueue.put(None)
        for t in self.verificationWorkerThreads:
            t.join()

        self.verifyDatafileRunnable = {}
        self.uploadDatafileRunnable = {}

        self.SetShuttingDown(False)

    def OnDropFiles(self, filepaths):
        if len(filepaths) == 1 and self.foldersModel.Contains(filepaths[0]):
            message = "This folder has already been added."
            dlg = wx.MessageDialog(None, message, "Add Folder(s)",
                                   wx.OK | wx.ICON_ERROR)
            dlg.ShowModal()
            return
        allFolders = True
        folderType = None
        folderModelsAdded = []
        for filepath in filepaths:

            if not os.path.isdir(filepath):
                message = filepath + " is not a folder."
                dlg = wx.MessageDialog(None, message, "Add Folder(s)",
                                       wx.OK | wx.ICON_ERROR)
                dlg.ShowModal()
                return
            elif not self.foldersModel.Contains(filepath):
                (location, folder) = os.path.split(filepath)

                dataViewId = self.foldersModel.GetMaxDataViewId() + 1
                if folderType is None:

                    usersModel = self.usersModel
                    addFolderDialog = \
                        AddFolderDialog(self.notifyWindow, -1,
                                        "Add Folder(s)", usersModel,
                                        size=(350, 200),
                                        style=wx.DEFAULT_DIALOG_STYLE)
                    if self.lastUsedFolderType is not None:
                        addFolderDialog\
                            .SetFolderType(GetFolderTypes()
                                           .index(self.lastUsedFolderType))

                    addFolderDialog.CenterOnParent()

                    if addFolderDialog.ShowModal() == wx.ID_OK:
                        folderType = addFolderDialog.GetFolderType()
                        self.lastUsedFolderType = folderType
                    else:
                        return

                usersModel = self.usersModel
                owner = \
                    usersModel.GetUserByName(addFolderDialog.GetOwnerName())
                settingsModel = self.foldersModel.GetSettingsModel()
                folderModel = FolderModel(dataViewId=dataViewId, folder=folder,
                                          location=location,
                                          folder_type=self.lastUsedFolderType,
                                          owner_id=owner.GetId(),
                                          foldersModel=self.foldersModel,
                                          usersModel=usersModel,
                                          settingsModel=settingsModel)
                self.foldersModel.AddRow(folderModel)
                folderModelsAdded.append(folderModel)

        self.StartDataUploads(folderModels=folderModelsAdded)

    def VerifyDatafiles(self, folderModel):
        if folderModel not in self.verifyDatafileRunnable:
            self.verifyDatafileRunnable[folderModel] = []
        for dfi in range(0, folderModel.numFiles):
            if self.IsShuttingDown():
                return
            thisFileIsAlreadyBeingVerified = False
            for existingVerifyDatafileRunnable in \
                    self.verifyDatafileRunnable[folderModel]:
                if dfi == existingVerifyDatafileRunnable.GetDatafileIndex():
                    thisFileIsAlreadyBeingVerified = True
            thisFileIsAlreadyBeingUploaded = False
            if folderModel in self.uploadDatafileRunnable:
                if dfi in self.uploadDatafileRunnable[folderModel]:
                    thisFileIsAlreadyBeingUploaded = True
            if not thisFileIsAlreadyBeingVerified \
                    and not thisFileIsAlreadyBeingUploaded:
                self.verifyDatafileRunnable[folderModel]\
                    .append(VerifyDatafileRunnable(self, self.foldersModel,
                                                   folderModel, dfi,
                                                   self.settingsModel))
                self.verificationsQueue\
                    .put(self.verifyDatafileRunnable[folderModel][dfi])

    def OnAddFolder(self, evt):
        dlg = wx.DirDialog(self.notifyWindow, "Choose a directory:")
        if dlg.ShowModal() == wx.ID_OK:
            self.OnDropFiles([dlg.GetPath(), ])

    def OnDeleteFolders(self, evt):
        # Remove the selected row(s) from the model. The model will take care
        # of notifying the view (and any other observers) that the change has
        # happened.
        items = self.foldersView.GetDataViewControl().GetSelections()
        rows = [self.foldersModel.GetRow(item) for item in items]
        if len(rows) > 1:
            message = "Are you sure you want to remove the selected folders?"
        elif len(rows) == 1:
            message = "Are you sure you want to remove the \"" + \
                self.foldersModel.GetValueForRowColname(rows[0], "Folder") + \
                "\" folder?"
        else:
            dlg = wx.MessageDialog(self.notifyWindow,
                                   "Please select a folder to delete.",
                                   "Delete Folder(s)", wx.OK)
            dlg.ShowModal()
            return
        confirmationDialog = \
            wx.MessageDialog(self.notifyWindow, message, "Confirm Delete",
                             wx.OK | wx.CANCEL | wx.ICON_QUESTION)
        okToDelete = confirmationDialog.ShowModal()
        if okToDelete == wx.ID_OK:
            self.foldersModel.DeleteRows(rows)

    def OnOpenFolder(self, evt):
        items = self.foldersView.GetDataViewControl().GetSelections()
        rows = [self.foldersModel.GetRow(item) for item in items]
        if len(rows) != 1:
            if len(rows) > 1:
                message = "Please select a single folder."
            else:
                message = "Please select a folder to open."
            dlg = wx.MessageDialog(self.notifyWindow, message, "Open Folder",
                                   wx.OK)
            dlg.ShowModal()
            return
        row = rows[0]

        path = os.path.join(self.foldersModel
                            .GetValueForRowColname(row, "Location"),
                            self.foldersModel
                            .GetValueForRowColname(row, "Folder"))
        if not os.path.exists(path):
            message = "Path doesn't exist: " + path
            dlg = wx.MessageDialog(None, message, "Open Folder", wx.OK)
            dlg.ShowModal()
            return
        if sys.platform == 'darwin':
            def openFolder(path):
                subprocess.check_call(['open', '--', path])
        elif sys.platform.startswith('linux'):
            def openFolder(path):
                subprocess.check_call(['xdg-open', '--', path])
        elif sys.platform.startswith('win'):
            def openFolder(path):
                subprocess.call(['explorer', path])
        else:
            logger.debug("sys.platform = " + sys.platform)

        openFolder(path)

    def CalculateMd5Sum(self, filePath, fileSize, uploadModel,
                        progressCallback=None):
        md5 = hashlib.md5()
        chunkSize = 102400
        oneMegabyte = 1048576
        while (fileSize / chunkSize) > 100 and chunkSize < oneMegabyte:
            chunkSize = chunkSize * 2
        bytesProcessed = 0
        with open(filePath, 'rb') as f:
            # Note that the iter() func needs an empty byte string
            # for the returned iterator to halt at EOF, since read()
            # returns b'' (not just '').
            for chunk in iter(lambda: f.read(chunkSize), b''):
                if self.IsShuttingDown() or uploadModel.Canceled():
                    logger.debug("Aborting MD5 calculation for "
                                 "%s" % filePath)
                    return None
                md5.update(chunk)
                bytesProcessed += len(chunk)
                if progressCallback:
                    progressCallback(bytesProcessed)
        return md5.hexdigest()


class VerifyDatafileRunnable():
    def __init__(self, foldersController, foldersModel, folderModel,
                 dataFileIndex, settingsModel):
        self.foldersController = foldersController
        self.foldersModel = foldersModel
        self.folderModel = folderModel
        self.dataFileIndex = dataFileIndex
        self.settingsModel = settingsModel

    def GetDatafileIndex(self):
        return self.dataFileIndex

    def run(self):
        dataFilePath = self.folderModel.GetDataFilePath(self.dataFileIndex)
        dataFileDirectory = \
            self.folderModel.GetDataFileDirectory(self.dataFileIndex)
        dataFileName = os.path.basename(dataFilePath)

        existingDatafile = None
        try:
            existingDatafile = DataFileModel.GetDataFile(
                settingsModel=self.settingsModel,
                dataset=self.folderModel.GetDatasetModel(),
                filename=dataFileName,
                directory=dataFileDirectory)
        except DoesNotExist, e:
            wx.PostEvent(
                self.foldersController.notifyWindow,
                self.foldersController.DidntFindMatchingDatafileOnServerEvent(
                    foldersController=self.foldersController,
                    folderModel=self.folderModel,
                    dataFileIndex=self.dataFileIndex))
        except MultipleObjectsReturned, e:
            self.folderModel.SetDataFileUploaded(self.dataFileIndex, True)
            self.foldersModel.FolderStatusUpdated(self.folderModel)
            logger.error(e.GetMessage())
            raise
        except:
            logger.error(traceback.format_exc())

        if existingDatafile:
            self.folderModel.SetDataFileUploaded(self.dataFileIndex, True)
            self.foldersModel.FolderStatusUpdated(self.folderModel)
            replicas = existingDatafile.GetReplicas()
            if len(replicas) == 0 or not replicas[0].IsVerified():
                logger.info("Found datafile record for %s "
                            "but it has no verified replicas."
                            % dataFilePath)
                logger.debug(str(existingDatafile.GetJson()))
                uploadToStagingRequest = self.settingsModel\
                    .GetUploadToStagingRequest()
                bytesUploadedToStaging = 0
                if self.foldersController.uploadMethod == \
                        UploadMethod.CAT_SSH and \
                        uploadToStagingRequest is not None and \
                        uploadToStagingRequest['approved'] and \
                        len(replicas) > 0:
                    username = uploadToStagingRequest['approved_username']
                    privateKeyFilePath = self.settingsModel\
                        .GetSshKeyPair().GetPrivateKeyFilePath()
                    hostJson = \
                        uploadToStagingRequest['approved_staging_host']
                    host = hostJson['host']
                    bytesUploadedToStaging = \
                        OpenSSH.GetBytesUploadedToStaging(
                            replicas[0].GetUri(),
                            username, privateKeyFilePath, host)
                if bytesUploadedToStaging == int(existingDatafile.GetSize()):
                    logger.debug("No need to re-upload \"%s\" to staging. "
                                 "The file size is correct in staging."
                                 % dataFilePath)
                    return
                else:
                    logger.debug("Re-uploading \"%s\" to staging, because "
                                 "the file size is %d bytes in staging, but "
                                 "it should be %d bytes."
                                 % (dataFilePath,
                                    bytesUploadedToStaging, 
                                    int(existingDatafile.GetSize())))
                wx.PostEvent(
                    self.foldersController.notifyWindow,
                    self.foldersController.UnverifiedDatafileOnServerEvent(
                        foldersController=self.foldersController,
                        folderModel=self.folderModel,
                        dataFileIndex=self.dataFileIndex,
                        existingUnverifiedDatafile=existingDatafile))


class UploadDatafileRunnable():
    def __init__(self, foldersController, foldersModel, folderModel,
                 dataFileIndex, uploadsModel, uploadModel, settingsModel,
                 existingUnverifiedDatafile):
        self.foldersController = foldersController
        self.foldersModel = foldersModel
        self.folderModel = folderModel
        self.dataFileIndex = dataFileIndex
        self.uploadsModel = uploadsModel
        self.uploadModel = uploadModel
        self.settingsModel = settingsModel
        self.existingUnverifiedDatafile = existingUnverifiedDatafile

    def GetDatafileIndex(self):
        return self.dataFileIndex

    def run(self):
        if self.uploadModel.Canceled():
            # self.foldersController.SetCanceled()
            logger.debug("Upload for \"%s\" was canceled "
                         "before it began uploading." %
                         self.uploadModel.GetRelativePathToUpload())
            return
        dataFilePath = self.folderModel.GetDataFilePath(self.dataFileIndex)
        dataFileName = os.path.basename(dataFilePath)
        dataFileDirectory = \
            self.folderModel.GetDataFileDirectory(self.dataFileIndex)
        datasetId = self.folderModel.GetDatasetModel().GetId()

        myTardisUrl = self.settingsModel.GetMyTardisUrl()
        myTardisUsername = self.settingsModel.GetUsername()
        myTardisApiKey = self.settingsModel.GetApiKey()

        logger.debug("Uploading " +
                     self.folderModel.GetDataFileName(self.dataFileIndex) +
                     "...")

        url = myTardisUrl + "/api/v1/dataset_file/"
        headers = {"Authorization": "ApiKey " + myTardisUsername + ":" +
                   myTardisApiKey}

        if self.foldersController.IsShuttingDown():
            return
        self.uploadModel.SetMessage("Getting data file size...")
        # ValueError: I/O operation on closed file
        dataFileSize = self.folderModel.GetDataFileSize(self.dataFileIndex)
        self.uploadModel.SetFileSize(dataFileSize)

        if self.foldersController.IsShuttingDown():
            return
        self.uploadModel.SetMessage("Calculating MD5 checksum...")
        def md5ProgressCallback(bytesProcessed):
            if self.uploadModel.Canceled():
                # self.foldersController.SetCanceled()
                return
            percentComplete = 100 - ((dataFileSize - bytesProcessed) * 100) \
                / (dataFileSize)

            self.uploadModel.SetProgress(float(percentComplete))
            self.uploadsModel.UploadProgressUpdated(self.uploadModel)
            self.uploadModel.SetMessage("%3d %%  MD5 summed"
                                        % int(percentComplete))
            self.uploadsModel.UploadMessageUpdated(self.uploadModel)
            myTardisUrl = self.settingsModel.GetMyTardisUrl()
            wx.PostEvent(
                self.foldersController.notifyWindow,
                self.foldersController.ConnectionStatusEvent(
                    myTardisUrl=myTardisUrl,
                    connectionStatus=ConnectionStatus.CONNECTED))
        dataFileMd5Sum = \
            self.foldersController\
                .CalculateMd5Sum(dataFilePath, dataFileSize, self.uploadModel,
                                 progressCallback=md5ProgressCallback)

        if self.uploadModel.Canceled():
            # self.foldersController.SetCanceled()
            logger.debug("Upload for \"%s\" was canceled "
                         "before it began uploading." %
                         self.uploadModel.GetRelativePathToUpload())
            return

        self.uploadModel.SetProgress(0.0)
        self.uploadsModel.UploadProgressUpdated(self.uploadModel)
        if dataFileSize == 0:
            self.uploadsModel.UploadFileSizeUpdated(self.uploadModel)
            self.uploadModel.SetMessage("MyTardis will not accept a "
                                        "data file with a size of zero.")
            self.uploadsModel.UploadMessageUpdated(self.uploadModel)
            self.uploadModel.SetStatus(UploadStatus.FAILED)
            self.uploadsModel.UploadStatusUpdated(self.uploadModel)
            return

        if self.foldersController.IsShuttingDown():
            return
        self.uploadModel.SetMessage("Checking MIME type...")
        # mimetypes.guess_type(...) is not thread-safe!
        mimeTypes = mimetypes.MimeTypes()
        dataFileMimeType = mimeTypes.guess_type(dataFilePath)[0]

        if self.foldersController.IsShuttingDown():
            return
        self.uploadModel.SetMessage("Defining JSON data for POST...")
        datasetUri = self.folderModel.GetDatasetModel().GetResourceUri()
        dataFileCreatedTime = \
            self.folderModel.GetDataFileCreatedTime(self.dataFileIndex)
        dataFileJson = {"dataset": datasetUri,
                        "filename": dataFileName,
                        "directory": dataFileDirectory,
                        "md5sum": dataFileMd5Sum,
                        "size": dataFileSize,
                        "mimetype": dataFileMimeType,
                        "created_time": dataFileCreatedTime}

        if self.uploadModel.Canceled():
            # self.foldersController.SetCanceled()
            logger.debug("Upload for \"%s\" was canceled "
                         "before it began uploading." %
                         self.uploadModel.GetRelativePathToUpload())
            return
        if self.foldersController.uploadMethod == UploadMethod.HTTP_POST:
            self.uploadModel.SetMessage("Initializing buffered reader...")
            datafileBufferedReader = io.open(dataFilePath, 'rb')
            self.uploadModel.SetBufferedReader(datafileBufferedReader)

        def progressCallback(param, current, total):
            if self.uploadModel.Canceled():
                # self.foldersController.SetCanceled()
                return
            percentComplete = 100 - ((total - current) * 100) / (total)

            self.uploadModel.SetBytesUploaded(current)
            self.uploadModel.SetProgress(float(percentComplete))
            self.uploadsModel.UploadProgressUpdated(self.uploadModel)
            self.uploadModel.SetMessage("%3d %%  uploaded"
                                        % int(percentComplete))
            self.uploadsModel.UploadMessageUpdated(self.uploadModel)
            myTardisUrl = self.settingsModel.GetMyTardisUrl()
            wx.PostEvent(
                self.foldersController.notifyWindow,
                self.foldersController.ConnectionStatusEvent(
                    myTardisUrl=myTardisUrl,
                    connectionStatus=ConnectionStatus.CONNECTED))

        # FIXME: The database interactions below should go in a model class.

        if self.foldersController.uploadMethod == UploadMethod.CAT_SSH:
            headers = {"Authorization": "ApiKey " +
                       myTardisUsername + ":" + myTardisApiKey,
                       "Content-Type": "application/json",
                       "Accept": "application/json"}
            data = json.dumps(dataFileJson)
        else:
            datagen, headers = poster.encode.multipart_encode(
                {"json_data": json.dumps(dataFileJson),
                 "attached_file": datafileBufferedReader},
                cb=progressCallback)
            opener = poster.streaminghttp.register_openers()
            opener.addheaders = [("Authorization", "ApiKey " +
                                  myTardisUsername +
                                  ":" + myTardisApiKey),
                                 ("Content-Type", "application/json"),
                                 ("Accept", "application/json")]

        self.uploadModel.SetMessage("Uploading...")
        postSuccess = False
        uploadSuccess = False

        request = None
        response = None
        try:
            if self.foldersController.uploadMethod == UploadMethod.HTTP_POST:
                request = urllib2.Request(url, datagen, headers)
            try:
                if self.foldersController.uploadMethod == \
                        UploadMethod.HTTP_POST:
                    response = urllib2.urlopen(request)
                    postSuccess = True
                    uploadSuccess = True
                else:
                    if not self.existingUnverifiedDatafile:
                        response = requests.post(headers=headers, url=url,
                                                 data=data)
                        postSuccess = response.status_code >= 200 and \
                            response.status_code < 300
                    if postSuccess or self.existingUnverifiedDatafile:
                        uploadToStagingRequest = self.settingsModel\
                            .GetUploadToStagingRequest()
                        hostJson = \
                            uploadToStagingRequest['approved_staging_host']
                        host = hostJson['host']
                        username = uploadToStagingRequest['approved_username']
                        privateKeyFilePath = self.settingsModel\
                            .GetSshKeyPair().GetPrivateKeyFilePath()
                        if self.existingUnverifiedDatafile:
                            uri = self.existingUnverifiedDatafile\
                                .GetReplicas()[0].GetUri()
                        else:
                            uri = response.json()['replicas'][0]['uri']
                            # logger.debug(response.text)
                        OpenSSH.UploadFile(dataFilePath, dataFileSize,
                                           username, privateKeyFilePath,
                                           host, uri,
                                           progressCallback,
                                           self.foldersController,
                                           self.uploadModel)
                        if self.uploadModel.GetBytesUploaded() == dataFileSize:
                            uploadSuccess = True
                        else:
                            raise Exception(
                                "Only %d of %d bytes were uploaded for %s"
                                % (self.uploadModel.GetBytesUploaded(),
                                   dataFileSize,
                                   dataFilePath))
                    if not postSuccess and not self.existingUnverifiedDatafile:
                        if response.status_code == 401:
                            message = "Couldn't create datafile \"%s\" " \
                                      "for folder \"%s\"." \
                                      % (dataFileName,
                                         self.folderModel.GetFolder())
                            message += "\n\n"
                            message += \
                                "Please ask your MyTardis administrator to " \
                                "check the permissions of the \"%s\" user " \
                                "account." % myTardisDefaultUsername
                            raise Unauthorized(message)
                        elif response.status_code == 404:
                            message = "Encountered a 404 (Not Found) error " \
                                "while attempting to create a datafile " \
                                "record for \"%s\" in folder \"%s\"." \
                                      % (dataFileName,
                                         self.folderModel.GetFolder())
                            message += "\n\n"
                            message += \
                                "Please ask your MyTardis administrator to " \
                                "check whether an appropriate staging " \
                                "storage box exists."
                            raise DoesNotExist(message)
                        elif response.status_code == 500:
                            message = "Couldn't create datafile \"%s\" " \
                                      "for folder \"%s\"." \
                                      % (dataFileName,
                                         self.folderModel.GetFolder())
                            message += "\n\n"
                            message += "An Internal Server Error occurred."
                            message += "\n\n"
                            message += \
                                "If running MyTardis in DEBUG mode, " \
                                "more information may be available below. " \
                                "Otherwise, please ask your MyTardis " \
                                "administrator to check in their logs " \
                                "for more information."
                            message += "\n\n"
                            try:
                                message += "ERROR: \"%s\"" \
                                    % response.json()['error_message']
                            except:
                                message = "Internal Server Error: " \
                                    "See MyData's log for further " \
                                    "information."
                            raise InternalServerError(message)
                        else:
                            # FIXME: If POST fails for some other reason,
                            # for now, we will just populate the upload's
                            # message field with an error message, and
                            # allow the other uploads to continue.  There
                            # may be other critical errors where we should
                            # raise an exception and abort all uploads.
                            pass
            except DoesNotExist, e:
                # This generally means that MyTardis's API couldn't assign
                # a staging storage box, possibly because the MyTardis
                # administrator hasn't created a storage box record with
                # the correct storage box attribute, i.e.
                # (key="Staging", value=True). The staging storage box should
                # also have a storage box option with
                # (key="location", value="/mnt/.../MYTARDIS_STAGING")
                wx.PostEvent(
                    self.foldersController.notifyWindow,
                    self.foldersController.AbortUploadsEvent())
                message = str(e)
                wx.PostEvent(
                    self.foldersController.notifyWindow,
                    self.foldersController
                        .ShowMessageDialogEvent(
                            title="MyData",
                            message=message,
                            icon=wx.ICON_ERROR))
                return
            except ValueError, e:
                if str(e) == "read of closed file" or \
                        str(e) == "seek of closed file":
                    logger.debug("Aborting upload for \"%s\" because "
                                 "file handle was closed." %
                                 self.uploadModel.GetRelativePathToUpload())
                    return
                else:
                    raise
        except Exception, e:
            if not self.foldersController.IsShuttingDown():
                wx.PostEvent(
                    self.foldersController.notifyWindow,
                    self.foldersController.ConnectionStatusEvent(
                        myTardisUrl=self.settingsModel.GetMyTardisUrl(),
                        connectionStatus=ConnectionStatus.DISCONNECTED))

            self.uploadModel.SetMessage(str(e))
            self.uploadsModel.UploadMessageUpdated(self.uploadModel)
            self.uploadModel.SetStatus(UploadStatus.FAILED)
            self.uploadsModel.UploadStatusUpdated(self.uploadModel)
            if dataFileDirectory != "":
                logger.debug("Upload failed for datafile " + dataFileName +
                             " in subdirectory " + dataFileDirectory +
                             " of folder " + self.folderModel.GetFolder())
            else:
                logger.debug("Upload failed for datafile " + dataFileName +
                             " of folder " + self.folderModel.GetFolder())
            logger.debug(url)
            if hasattr(e, "code"):
                logger.error(e.code)
            logger.error(str(e))
            if self.foldersController.uploadMethod == \
                    UploadMethod.HTTP_POST:
                if request is not None:
                    logger.error(str(request.header_items()))
                else:
                    logger.error("request is None.")
            if response is not None:
                if self.foldersController.uploadMethod == \
                        UploadMethod.HTTP_POST:
                    logger.debug(response.read())
                else:
                    # logger.debug(response.text)
                    pass
            else:
                logger.error("response is None.")
            self.uploadModel.SetMessage(str(e))
            self.uploadsModel.UploadMessageUpdated(self.uploadModel)
            if hasattr(e, "headers"):
                logger.debug(str(e.headers))
            if hasattr(response, "headers"):
                # logger.debug(str(response.headers))
                pass
            logger.debug(traceback.format_exc())
            return

        if uploadSuccess:
            logger.debug("Upload succeeded for " + dataFilePath)
            self.uploadModel.SetStatus(UploadStatus.COMPLETED)
            self.uploadsModel.UploadStatusUpdated(self.uploadModel)
            self.uploadModel.SetMessage("Upload complete!")
            self.uploadModel.SetProgress(100.0)
            self.uploadsModel.UploadProgressUpdated(self.uploadModel)
            self.folderModel.SetDataFileUploaded(self.dataFileIndex,
                                                 uploaded=True)
            self.foldersModel.FolderStatusUpdated(self.folderModel)
        else:
            logger.error("Upload failed for " + dataFilePath)
            self.uploadModel.SetStatus(UploadStatus.FAILED)
            self.uploadsModel.UploadStatusUpdated(self.uploadModel)
            if not postSuccess and response is not None:
                message = "Internal Server Error: " \
                    "See MyData's log for further " \
                    "information."
                logger.error(message)
                self.uploadModel.SetMessage(response.text)
            else:
                self.uploadModel.SetMessage("Upload failed.")

            self.uploadModel.SetProgress(0.0)
            self.uploadsModel.UploadProgressUpdated(self.uploadModel)
            self.folderModel.SetDataFileUploaded(self.dataFileIndex,
                                                 uploaded=False)
            self.foldersModel.FolderStatusUpdated(self.folderModel)
