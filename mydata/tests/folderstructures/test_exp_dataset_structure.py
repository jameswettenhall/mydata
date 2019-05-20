"""
Test ability to scan the Experiment / Dataset folder structure.
"""
from .. import MyDataScanFoldersTester
from .. import ValidateSettingsAndScanFolders


class ScanExpDatasetTester(MyDataScanFoldersTester):
    """
    Test ability to scan the Experiment / Dataset folder structure.
    """
    def test_scan_folders(self):
        """Test ability to scan the Experiment / Dataset folder structure.
        """
        self.UpdateSettingsFromCfg("testdataExpDataset")
        ValidateSettingsAndScanFolders()
        self.AssertFolders(["Birds", "Flowers"])
        self.AssertNumFiles(5)
