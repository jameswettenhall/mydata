"""
Test ability to perform connectivity check.
"""
import unittest
import wx

from mydata.models.settings import SettingsModel
import mydata.events as mde


class ConnectivityCheckTester(unittest.TestCase):
    """
    Test ability to perform connectivity check.
    """
    def setUp(self):
        self.app = wx.App()
        self.frame = wx.Frame(parent=None, id=wx.ID_ANY,
                              title="Connectivity check test")
        mde.MYDATA_EVENTS.InitializeWithNotifyWindow(self.frame)
        self.settingsModel = SettingsModel(configPath=None)

    def tearDown(self):
        self.frame.Destroy()

    def test_connectivity_check(self):
        """
        Test ability to perform connectivity check.
        """
        event = mde.MYDATA_EVENTS.CheckConnectivityEvent(
            settingsModel=self.settingsModel)
        mde.CheckConnectivity(event)


if __name__ == '__main__':
    unittest.main()