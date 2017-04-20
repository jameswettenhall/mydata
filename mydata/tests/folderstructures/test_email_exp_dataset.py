"""
Test ability to scan the Email / Experiment / Dataset folder structure.
"""
from .. import MyDataScanFoldersTester


class ScanEmailExpDatasetTester(MyDataScanFoldersTester):
    """
    Test ability to scan the Email / Experiment / Dataset folder structure.
    """
    def setUp(self):
        super(ScanEmailExpDatasetTester, self).setUp()
        super(ScanEmailExpDatasetTester, self).InitializeAppAndFrame(
            'ScanEmailExpDatasetTester')

    def test_scan_folders(self):
        """
        Test ability to scan the Email / Experiment / Dataset folder structure.
        """
        self.UpdateSettingsFromCfg("testdataEmailExpDataset")
        self.ValidateSettingsAndScanFolders()
        self.AssertUsers(["testuser1", "testuser2"])
        self.AssertFolders(["Birds", "Flowers"])
        self.AssertNumFiles(5)
