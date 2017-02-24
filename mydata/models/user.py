"""
Model class for MyTardis API v1's UserResource.
See: https://github.com/mytardis/mytardis/blob/3.7/tardis/tardis_portal/api.py
"""
import traceback
import urllib
import requests

from mydata.utils.exceptions import DoesNotExist
from mydata.logs import logger
from .group import GroupModel


class UserModel(object):
    """
    Model class for MyTardis API v1's UserResource.
    See: https://github.com/mytardis/mytardis/blob/3.7/tardis/tardis_portal/api.py
    """
    # pylint: disable=missing-docstring
    # pylint: disable=too-many-instance-attributes
    userNotFoundString = "USER NOT FOUND IN MYTARDIS"

    def __init__(self, settingsModel=None, dataViewId=None,
                 username=None, name=None,
                 email=None, userRecordJson=None,
                 userNotFoundInMyTardis=False):
        self.settingsModel = settingsModel
        self.userId = None
        self.dataViewId = dataViewId
        self.username = username
        self.name = name
        self.email = email
        self.groups = []
        self.userRecordJson = userRecordJson
        self.userNotFoundInMyTardis = userNotFoundInMyTardis

        if userRecordJson is not None:
            self.userId = userRecordJson['id']
            if username is None:
                self.username = userRecordJson['username']
            if name is None:
                self.name = userRecordJson['first_name'] + " " + \
                    userRecordJson['last_name']
            if email is None:
                self.email = userRecordJson['email']
            for group in userRecordJson['groups']:
                self.groups.append(GroupModel(settingsModel=settingsModel,
                                              groupJson=group))

    def GetId(self):
        return self.userId

    def GetDataViewId(self):
        return self.dataViewId

    def SetDataViewId(self, dataViewId):
        self.dataViewId = dataViewId

    def GetUsername(self):
        if self.username:
            return self.username
        else:
            return UserModel.userNotFoundString

    def GetName(self):
        if self.name:
            return self.name
        else:
            return UserModel.userNotFoundString

    def GetEmail(self):
        if self.email:
            return self.email
        else:
            return UserModel.userNotFoundString

    def GetGroups(self):
        return self.groups

    def GetValueForKey(self, key):
        if self.__dict__[key]:
            return self.__dict__[key]
        if key in ('username', 'name', 'email') and \
                self.UserNotFoundInMyTardis():
            return UserModel.userNotFoundString
        else:
            return None

    def GetJson(self):
        return self.userRecordJson

    def UserNotFoundInMyTardis(self):
        return self.userNotFoundInMyTardis

    @staticmethod
    def GetUserByUsername(settings, username):
        url = "%s/api/v1/user/?format=json&username=%s" \
            % (settings.general.myTardisUrl, username)
        try:
            response = requests.get(url=url, headers=settings.defaultHeaders)
        except:
            raise Exception(traceback.format_exc())
        if response.status_code != 200:
            message = response.text
            raise Exception(message)
        try:
            userRecordsJson = response.json()
        except:
            logger.error(traceback.format_exc())
            raise
        numUserRecordsFound = userRecordsJson['meta']['total_count']

        if numUserRecordsFound == 0:
            raise DoesNotExist(
                message="User \"%s\" was not found in MyTardis" % username,
                response=response)
        else:
            logger.debug("Found user record for username '" + username + "'.")
            return UserModel(settingsModel=settings, username=username,
                             userRecordJson=userRecordsJson['objects'][0])

    @staticmethod
    def GetUserByEmail(settings, email):
        url = "%s/api/v1/user/?format=json&email__iexact=%s" \
            % (settings.general.myTardisUrl,
               urllib.quote(email.encode('utf-8')))
        try:
            response = requests.get(url=url, headers=settings.defaultHeaders)
        except:
            raise Exception(traceback.format_exc())
        if response.status_code != 200:
            logger.debug(url)
            message = response.text
            raise Exception(message)
        try:
            userRecordsJson = response.json()
        except:
            logger.error(traceback.format_exc())
            raise
        numUserRecordsFound = userRecordsJson['meta']['total_count']

        if numUserRecordsFound == 0:
            raise DoesNotExist(
                message="User with email \"%s\" was not found in MyTardis"
                % email,
                response=response)
        else:
            logger.debug("Found user record for email '" + email + "'.")
            return UserModel(settingsModel=settings,
                             userRecordJson=userRecordsJson['objects'][0])


class UserProfileModel(object):
    """
    Used with the DoesNotExist exception when a 404 from MyTardis's API
    is assumed to have been caused by a missing user profile record.
    """
    pass
