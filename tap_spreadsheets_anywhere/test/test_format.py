import codecs
import unittest

import dateutil
import smart_open

from tap_spreadsheets_anywhere import configuration, file_utils, csv_handler, json_handler
from tap_spreadsheets_anywhere.format_handler import monkey_patch_streamreader, get_row_iterator

TEST_TABLE_SPEC = {
    "tables": [
        {
            "path": "s3://any_bucket_willdo",
            "name": "products",
            "pattern": "g2/.*roduct.*",
            "start_date": "2017-05-01T00:00:00Z",
            "key_properties": ["id"],
            "format": "csv",
            "prefer_number_vs_integer": True,
            "universal_newlines": False,
            "sample_rate": 5,
            "max_sampling_read": 2000,
            "max_sampled_files": 3,
            "schema_overrides": {
                "id": {
                    "type": "integer"
                }
            }
        },
        {
            "path": "file://./artifacts",
            "name": "badnewlines",
            "pattern": '.*\\.csv',
            "start_date": "2017-05-01T00:00:00Z",
            "key_properties": [],
            "format": "csv",
            "universal_newlines": False,
            "sample_rate": 5,
            "max_sampling_read": 2000,
            "max_sampled_files": 3
        },
        {
            "path": "file://./tap_spreadsheets_anywhere/test",
            "name": "badnewlines",
            "pattern": ".*\\.xlsx",
            "start_date": "2017-05-01T00:00:00Z",
            "key_properties": [],
            "format": "excel",
            "worksheet_name": "sample_with_bad_newlines"
        },
        {
            "path": "file://./tap_spreadsheets_anywhere/test",
            "name": "badnewlines",
            "pattern": ".*\\.json",
            "start_date": "2017-05-01T00:00:00Z",
            "key_properties": [],
            "format": "detect"
        },
        {
            "path": "https://www.treasury.gov/ofac/downloads",
            "name": "sdn",
            "pattern": "sdn.csv",
            "start_date": "1970-05-01T00:00:00Z",
            "key_properties": [],
            "format": "csv",
            "field_names": ["id","name","a" ,"country","b" ,"c" ,"d" ,"e" ,"f" ,"g" ,"h" ,"i"]
        },
        {
            "path": "https://dataverse.harvard.edu/api/access/datafile/",
            "name": "dataverse",
            "pattern": "4202836",
            "start_date": "1970-05-01T00:00:00Z",
            "key_properties": [],
            "format": "csv"
        }
    ]
}


class TestFormatHandler(unittest.TestCase):

    def test_custom_config(self):
        configuration.CONFIG_CONTRACT(TEST_TABLE_SPEC)

    def test_handle_newlines_local_excel(self):
        test_filename_uri = './tap_spreadsheets_anywhere/test/excel_with_bad_newlines.xlsx'
        iterator = get_row_iterator(TEST_TABLE_SPEC['tables'][2], test_filename_uri)

        for row in iterator:
            self.assertTrue(isinstance(row['id'], float) or isinstance(row['id'], int),
                            "Parsed ID is not a number for: {}".format(row['id']))

    def test_handle_newlines_local_json(self):
        test_filename_uri = './tap_spreadsheets_anywhere/test/sample.json'
        iterator = get_row_iterator(TEST_TABLE_SPEC['tables'][3], test_filename_uri)

        for row in iterator:
            self.assertTrue(isinstance(row['id'], float) or isinstance(row['id'], int),
                            "Parsed ID is not a number for: {}".format(row['id']))

    def test_strip_newlines_local_custom_mini(self):
        test_filename_uri = './tap_spreadsheets_anywhere/test/sample_with_bad_newlines.csv'
        iterator = get_row_iterator(TEST_TABLE_SPEC['tables'][0], test_filename_uri)

        for row in iterator:
            self.assertTrue(row['id'].isnumeric(), "Parsed ID is not a number for: {}".format(row['id']))

    def test_strip_newlines_monkey_patch_locally(self):
        """Load the file in binary mode to force the use of StreamHandler and the monkey patch"""
        test_filename = './tap_spreadsheets_anywhere/test/sample_with_bad_newlines.csv'

        file_handle = smart_open.open(test_filename, 'rb', errors='surrogateescape')
        reader = codecs.getreader('utf-8')(file_handle)
        reader = monkey_patch_streamreader(reader)
        iterator = csv_handler.get_row_iterator(TEST_TABLE_SPEC['tables'][0], reader)

        for row in iterator:
            self.assertTrue(row['id'].isnumeric(), "Parsed ID is not a number for: {}".format(row['id']))

    def test_local_bucket(self):
        table_spec = TEST_TABLE_SPEC['tables'][1]
        modified_since = dateutil.parser.parse(table_spec['start_date'])
        target_files = file_utils.get_matching_objects(table_spec, modified_since)
        assert len(target_files) == 1

    def test_https_bucket(self):
        table_spec = TEST_TABLE_SPEC['tables'][4]
        modified_since = dateutil.parser.parse(table_spec['start_date'])
        target_files = file_utils.get_matching_objects(table_spec, modified_since)
        assert len(target_files) == 1

        target_uri = table_spec['path'] + '/' + target_files[0]["key"]
        iterator = get_row_iterator(TEST_TABLE_SPEC['tables'][4], target_uri)

        row = next(iterator)
        self.assertTrue(int(row['id']) > 0,row['id']+" was not positive")

    def test_indirect_https_bucket(self):
        table_spec = TEST_TABLE_SPEC['tables'][5]
        modified_since = dateutil.parser.parse(table_spec['start_date'])
        target_files = file_utils.get_matching_objects(table_spec, modified_since)
        assert len(target_files) == 1

        target_uri = table_spec['path'] + '/' + target_files[0]["key"]
        iterator = get_row_iterator(TEST_TABLE_SPEC['tables'][4], target_uri)

        row = next(iterator)
        self.assertTrue(int(row['id']) > 0,row['id']+" was not positive")