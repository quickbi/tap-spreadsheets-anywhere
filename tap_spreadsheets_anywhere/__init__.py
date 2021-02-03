#!/usr/bin/env python3
import os
import logging
import time

import dateutil
import singer
from singer import utils
from singer.catalog import Catalog, CatalogEntry
from singer.schema import Schema

from tap_spreadsheets_anywhere.configuration import Config
import tap_spreadsheets_anywhere.conversion as conversion
import tap_spreadsheets_anywhere.file_utils as file_utils

LOGGER = logging.getLogger(__name__)

def get_abs_path(path):
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), path)


def merge_dicts(first, second):
    to_return = first.copy()

    for key in second:
        if key in first:
            if isinstance(first[key], dict) and isinstance(second[key], dict):
                to_return[key] = merge_dicts(first[key], second[key])
            else:
                to_return[key] = second[key]
        else:
            to_return[key] = second[key]

    return to_return


def override_schema_with_config(inferred_schema, table_spec):
    override_schema = {'properties': table_spec.get('schema_overrides', {}),
                       'selected': table_spec.get('selected', True)}
    # Note that we directly support setting selected through config so that this tap is useful outside Meltano
    return merge_dicts(inferred_schema, override_schema)


def discover(config):
    streams = []
    for table_spec in config['tables']:
        try:
            modified_since = dateutil.parser.parse(table_spec['start_date'])
            target_files = file_utils.get_matching_objects(table_spec, modified_since)
            sample_rate = table_spec.get('sample_rate',10)
            max_sampling_read = table_spec.get('max_sampling_read', 1000)
            max_sampled_files = table_spec.get('max_sampled_files', 5)
            prefer_number_vs_integer = table_spec.get('prefer_number_vs_integer', False)
            samples = file_utils.sample_files(table_spec, target_files,sample_rate=sample_rate,
                                              max_records=max_sampling_read, max_files=max_sampled_files)

            metadata_schema = {
                '_smart_source_bucket': {'type': 'string'},
                '_smart_source_file': {'type': 'string'},
                '_smart_source_lineno': {'type': 'integer'},
            }
            data_schema = conversion.generate_schema(samples,prefer_number_vs_integer=prefer_number_vs_integer)
            inferred_schema = {
                'type': 'object',
                'properties': merge_dicts(data_schema, metadata_schema)
            }

            merged_schema = override_schema_with_config(inferred_schema, table_spec)
            schema = Schema.from_dict(merged_schema)

            stream_metadata = []
            key_properties = table_spec.get('key_properties', [])
            streams.append(
                CatalogEntry(
                    tap_stream_id=table_spec['name'],
                    stream=table_spec['name'],
                    schema=schema,
                    key_properties=key_properties,
                    metadata=stream_metadata,
                    replication_key=None,
                    is_view=None,
                    database=None,
                    table=None,
                    row_count=None,
                    stream_alias=None,
                    replication_method=None,
                )
            )
        except Exception as err:
            LOGGER.error(f"Unable to write Catalog entry for '{table_spec['name']}' - it will be skipped due to error {err}")

    return Catalog(streams)


def sync(config, state, catalog):
    # Loop over selected streams in catalog
    for stream in catalog.get_selected_streams(state):
        LOGGER.info("Syncing stream:" + stream.tap_stream_id)
        catalog_schema = stream.schema.to_dict()
        table_spec = next((x for x in config['tables'] if x['name'] == stream.tap_stream_id), None)
        if table_spec is not None:
            # Allow updates to our tables specification to override any previously extracted schema in the catalog
            merged_schema = override_schema_with_config(catalog_schema, table_spec)
            singer.write_schema(
                stream_name=stream.tap_stream_id,
                schema=merged_schema,
                key_properties=stream.key_properties,
            )

            full_table_replace = table_spec.get('full_table_replace', False)
            activate_version = None

            if full_table_replace:
                LOGGER.info(f'Use full table replace for Stream: {stream.tap_stream_id}')
                # Emit a Singer ACTIVATE_VERSION message before initial sync (but not subsequent syncs)
                # and everytime a sheet sync is complete.
                # This forces hard deletes on the data downstream if fewer records are sent.
                # https://github.com/singer-io/singer-python/blob/master/singer/messages.py#L137

                is_initial_sync = 'modified_since' not in state.get(stream.tap_stream_id, {})
                activate_version = int(time.time() * 1000)
                activate_version_message = singer.ActivateVersionMessage(
                    stream=stream.tap_stream_id,
                    version=activate_version,
                )
                if is_initial_sync:
                    # initial load, send ACTIVATE_VERSION message before the data sync
                    singer.write_message(activate_version_message)
                    LOGGER.info(f'INITIAL SYNC, Stream: {stream.tap_stream_id}, Activate Version: {activate_version}')

            modified_since = dateutil.parser.parse(
                state.get(stream.tap_stream_id, {}).get('modified_since') or table_spec['start_date'])
            target_files = file_utils.get_matching_objects(table_spec, modified_since)
            max_records_per_run = table_spec.get('max_records_per_run', -1)
            records_streamed = 0
            for t_file in target_files:
                records_streamed += file_utils.write_file(
                    t_file['key'],
                    table_spec,
                    merged_schema,
                    max_records=max_records_per_run-records_streamed,
                    version=activate_version,
                )
                if 0 < max_records_per_run <= records_streamed:
                    LOGGER.info(f'Processed the per-run limit of {records_streamed} records for stream "{stream.tap_stream_id}". Stopping sync for this stream.')
                    break
                state[stream.tap_stream_id] = {'modified_since': t_file['last_modified'].isoformat()}
                singer.write_state(state)

            if full_table_replace:
                # End of Stream: Send ACTIVATE_VERSION message
                singer.write_message(activate_version_message)
                LOGGER.info(f'COMPLETE SYNC, Stream: {stream.tap_stream_id}, Activate Version: {activate_version}')
            LOGGER.info(f'Wrote {records_streamed} records for stream "{stream.tap_stream_id}".')
        else:
            LOGGER.warn(f'Skipping processing for stream [{stream.tap_stream_id}] without a config block.')
    return

REQUIRED_CONFIG_KEYS = 'tables'

@utils.handle_top_exception(LOGGER)
def main():
    # Parse command line arguments
    args = utils.parse_args([REQUIRED_CONFIG_KEYS])
    crawl_paths = [x for x in args.config['tables'] if "crawl_config" in x and x["crawl_config"]]
    if len(crawl_paths) > 0: # Our config includes at least one crawl block
        LOGGER.info("Executing experimental 'crawl' mode to auto-generate a table config per bucket.")
        tables_config = file_utils.config_by_crawl(crawl_paths)
        # Add back in the non-crawl blocks
        tables_config['tables'] += [x for x in args.config['tables'] if "crawl_config" not in x or not x["crawl_config"]]
        crawl_results_file = "crawled-config.json"
        LOGGER.info(f"Writing expanded crawl blocks to {crawl_results_file}.")
        Config.dump(tables_config, open(crawl_results_file, "w"))
    else:
        tables_config = args.config

    tables_config = Config.validate(tables_config)
    # If discover flag was passed, run discovery mode and dump output to stdout
    if args.discover:
        catalog = discover(tables_config)
        catalog.dump()
    # Otherwise run in sync mode
    else:
        if args.catalog:
            catalog = args.catalog
            LOGGER.info(f"Using supplied catalog {args.catalog_path}.")
        else:
            LOGGER.info(f"Generating catalog through sampling.")
            catalog = discover(tables_config)
        sync(tables_config, args.state, catalog)

if __name__ == "__main__":
    main()
