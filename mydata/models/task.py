from datetime import datetime


class TaskModel():
    """
    A task can be a folder scan, datafile lookup and upload,
    or it could be a notification POSTed to MyTardis administrators.
    """
    def __init__(self, dataViewId, jobFunc, jobArgs, jobDesc, startTime,
                 intervalMinutes=None):
        self.dataViewId = dataViewId
        self.jobFunc = jobFunc
        self.jobArgs = jobArgs
        self.jobDesc = jobDesc
        self.startTime = startTime
        self.finishTime = None
        self.intervalMinutes = intervalMinutes

    def GetDataViewId(self):
        return self.dataViewId

    def GetJobFunc(self):
        return self.jobFunc

    def GetJobArgs(self):
        return self.jobArgs

    def GetJobDesc(self):
        return self.jobDesc

    def GetStartTime(self):
        return self.startTime

    def GetFinishTime(self):
        return self.finishTime

    def GetIntervalMinutes(self):
        return self.intervalMinutes

    def SetFinishTime(self, finishTime):
        self.finishTime = finishTime

    def GetValueForKey(self, key):
        if self.__dict__[key]:
            return self.__dict__[key]
        else:
            return None