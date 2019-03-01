# stdlib imports
import glob
import sys
import os
import logging
import zipfile

# third party imports
import numpy as np
from obspy.core.stream import Stream
from obspy.geodetics import gps2dist_azimuth
import pandas as pd

# local imports
from gmprocess.exception import GMProcessException
from gmprocess.io.read import read_data
from gmprocess.process import process_config
from gmprocess.metrics.station_summary import StationSummary

# local imports
from gmprocess.config import get_config

DEFAULT_IMTS = ['PGA', 'PGV', 'SA(0.3)', 'SA(1.0)', 'SA(3.0)']
DEFAULT_IMCS = ['GREATER_OF_TWO_HORIZONTALS', 'CHANNELS']
EXT_IGNORE = [".gif", ".V3", ".csv", ".dis", ".abc"]
UNIT_PREFERENCE_ORDER = ['']


def directory_to_streams(directory):
    """Read in a directory of data to a list of streams.

    Note:
    If the directory only includes files that are readable by this library
    then the task is rather simple. However, often times data directories
    include subdirectories and/or zip files, which we try to crawl in a
    sensible fashion.

    Todo: Write summary file of ignored files, either because of an error
    or because they are not a readable ground motion format.

    Args:
        directory (str):
            Directory of ground motion files (streams).

    Returns:
        List of obspy streams.
    """
    logging.warning("This method is not yet functional. Exiting.")
    sys.exit(1)

    all_files = [os.path.join(directory, f) for f in os.listdir(directory)]

    # -------------------------------------------------------------------------
    # Flatten directoreis by crawling subdirectories and move files up to base
    # directory
    # -------------------------------------------------------------------------
    # *** TODO ***

    # -------------------------------------------------------------------------
    # Take care of any zip files and extract, trying to ensure that no files
    # get overwritten.
    # -------------------------------------------------------------------------
    all_files = [os.path.join(directory, f) for f in os.listdir(directory)]
    for f in all_files:
        base = _get_file_base(f)
        if zipfile.is_zipfile(f):
            print('Extracting %s...' % f)
            with zipfile.ZipFile(f, 'r') as zip:
                for m in zip.namelist():
                    zip.extract(m, directory)
                    if base not in m:
                        src = os.path.join(directory, m)
                        new_name = '%s_%s' % (base, m)
                        dst = os.path.join(directory, new_name)
                        if not os.path.exists(dst):
                            os.rename(src, dst)
                        else:
                            logging.warning(
                                'While extracting %s, file %s already exists.'
                                % (f, dst))

    # -------------------------------------------------------------------------
    # Read streams
    # -------------------------------------------------------------------------
    streams = []
    unprocessed_files = []
    unprocessed_file_errors = []
    for filepath in glob.glob(os.path.join(directory, "*")):
        try:
            streams += [read_data(filepath)]
        except Exception as ex:
            unprocessed_files += [filepath]
            unprocessed_file_errors += [ex]

    # Flatten streams to one trace per streams:
    trace_list = []
    for stream in streams:
        for trace in stream:
            if trace.stats.network == '' or str(trace.stats.network) == 'nan':
                trace.stats.network = 'ZZ'
            if str(trace.stats.location) == 'nan':
                trace.stats.location = ''
            if (
                trace.stats.location == '' or
                str(trace.stats.location) == 'nan'
            ):
                trace.stats.location = '--'
            trace_list += [trace]

    # Check for matches
    match_list = _match_traces(trace_list)

    # Select only preferred processing levels:
    filtered_matches1 = []
    for i in range(len(match_list)):
        process_levels = [
            trace_list[j].stats.standard['process_level'].upper()
            for j in match_list[i]
        ]
        if 'V1' in process_levels:
            fm = []
            for j, m in enumerate(match_list[i]):
                if process_levels[j] == 'V1':
                    fm.append(match_list[i][j])
        elif 'V2' in process_levels:
            fm = []
            for j, m in enumerate(match_list[i]):
                if process_levels[j] == 'V2':
                    fm.append(match_list[i][j])
        elif 'V0' in process_levels:
            fm = []
            for j, m in enumerate(match_list[i]):
                if process_levels[j] == 'V0':
                    fm.append(match_list[i][j])
        else:
            continue
        filtered_matches1.append(fm)

    # Select only preferred band codes
    filtered_matches2 = []
    for i in range(len(filtered_matches1)):
        band_codes = [
            trace_list[j].stats['channel'][0].upper()
            for j in filtered_matches1[i]
        ]
        # Prefer High Broad Band
        if 'H' in band_codes:
            fm = []
            for j, m in enumerate(filtered_matches1[i]):
                if band_codes[j] == 'H':
                    fm.append(filtered_matches1[i][j])
        # Then Broad Band
        elif 'B' in band_codes:
            fm = []
            for j, m in enumerate(filtered_matches1[i]):
                if band_codes[j] == 'B':
                    fm.append(filtered_matches1[i][j])
        else:
            continue
        filtered_matches2.append(fm)

    return streams


def directory_to_dataframe(directory, imcs=None, imts=None, epi_dist=None,
                           event_time=None, lat=None, lon=None, process=True):
    """Extract peak ground motions from list of Stream objects.
    Note: The PGM columns underneath each channel will be variable
    depending on the units of the Stream being passed in (velocity
    sensors can only generate PGV) and on the imtlist passed in by
    user. Spectral acceleration columns will be formatted as SA(0.3)
    for 0.3 second spectral acceleration, for example.
    Args:
        directory (str): Directory of ground motion files (streams).
        imcs (list): Strings designating desired components to create
                in table.
        imts (list): Strings designating desired PGMs to create
                in table.
        epi_dist (float): Epicentral distance for processsing. If not included,
                but the lat and lon are, the distance will be calculated.
        event_time (float): Time of the event, used for processing.
        lat (float): Epicentral latitude. Epicentral distance calculation.
        lon (float): Epicentral longitude. Epicentral distance calculation.
        process (bool): Process the stream using the config file.
    Returns:
        DataFrame: Pandas dataframe containing columns:
            - STATION Station code.
            - NAME Text description of station.
            - LOCATION Two character location code.
            - SOURCE Long form string containing source network.
            - NETWORK Short network code.
            - LAT Station latitude
            - LON Station longitude
            - DISTANCE Epicentral distance (km) (if epicentral lat/lon provided)
            - HN1 East-west channel (or H1) (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
            - HN2 North-south channel (or H2) (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
            - HNZ Vertical channel (or HZ) (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
            - GREATER_OF_TWO_HORIZONTALS (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
    """
    streams = []
    for filepath in glob.glob(os.path.join(directory, "*")):
        streams += [read_data(filepath)]
    grouped_streams = group_channels(streams)

    dataframe = streams_to_dataframe(
        grouped_streams, imcs=imcs, imts=imts, epi_dist=epi_dist,
        event_time=event_time, lat=lat, lon=lon, process=process)
    return dataframe


def group_channels(streams):
    """Consolidate streams for the same event.

    Checks to see if there are channels for one station in different
    streams, and groups them into one stream. Then streams are checked for
    duplicate channels (traces).

    Args:
        streams (list): List of Stream objects.

    Returns:
        list: List of Stream objects.
    """
    # Return the original stream if there is only one
    if len(streams) <= 1:
        return streams

    # Get the all traces
    trace_list = []
    for stream in streams:
        for trace in stream:
            if trace.stats.network == '' or str(trace.stats.network) == 'nan':
                trace.stats.network = 'ZZ'
            if str(trace.stats.location) == 'nan':
                trace.stats.location = ''
            if trace.stats.location == '' or str(trace.stats.location) == 'nan':
                trace.stats.location = '--'
            trace_list += [trace]

    # Create a list of duplicate traces and event matches
    duplicate_list = []
    match_list = []
    for idx1, trace1 in enumerate(trace_list):
        matches = []
        network = trace1.stats['network']
        station = trace1.stats['station']
        starttime = trace1.stats['starttime']
        endtime = trace1.stats['endtime']
        channel = trace1.stats['channel']
        location = trace1.stats['location']
        if 'units' in trace1.stats.standard:
            units = trace1.stats.standard['units']
        else:
            units = ''
        if 'process_level' in trace1.stats.standard:
            process_level = trace1.stats.standard['process_level']
        else:
            process_level = ''
        data = np.asarray(trace1.data)
        for idx2, trace2 in enumerate(trace_list):
            if idx1 != idx2 and idx1 not in duplicate_list:
                event_match = False
                duplicate = False
                if data.shape == trace2.data.shape:
                    try:
                        same_data = ((data == np.asarray(trace2.data)).all())
                    except AttributeError:
                        same_data = (data == np.asarray(trace2.data))
                else:
                    same_data = False
                if 'units' in trace2.stats.standard:
                    units2 = trace2.stats.standard['units']
                else:
                    units2 = ''
                if 'process_level' in trace2.stats.standard:
                    process_level2 = trace2.stats.standard['process_level']
                else:
                    process_level2 = ''
                if (
                    network == trace2.stats['network'] and
                    station == trace2.stats['station'] and
                    starttime == trace2.stats['starttime'] and
                    endtime == trace2.stats['endtime'] and
                    channel == trace2.stats['channel'] and
                    location == trace2.stats['location'] and
                    units == units2 and
                    process_level == process_level2 and
                    same_data
                ):
                    duplicate = True
                elif (
                    network == trace2.stats['network'] and
                    station == trace2.stats['station'] and
                    starttime == trace2.stats['starttime'] and
                    location == trace2.stats['location'] and
                    units == units2 and
                    process_level == process_level2
                ):
                    event_match = True
                if duplicate:
                    duplicate_list += [idx2]
                if event_match:
                    matches += [idx2]
        match_list += [matches]

    # Create an updated list of streams
    streams = []
    for idx, matches in enumerate(match_list):
        stream = Stream()
        grouped = False
        for match_idx in matches:
            if match_idx not in duplicate_list:
                if idx not in duplicate_list:
                    stream.append(trace_list[match_idx])
                    duplicate_list += [match_idx]
                    grouped = True
        if grouped:
            stream.append(trace_list[idx])
            duplicate_list += [idx]
            streams += [stream]

    # Check for ungrouped traces
    for idx, trace in enumerate(trace_list):
        if idx not in duplicate_list:
            stream = Stream()
            streams += [stream.append(trace)]
            logging.warning('One channel stream:\n%s' % (stream))

    # Check for streams with more than three channels
    for stream in streams:
        if len(stream) > 3:
            raise GMProcessException(
                'Stream with more than 3 channels:\n%s.' % (stream))

    return streams


def streams_to_dataframe(streams, imcs=None, imts=None,
                         epi_dist=None, event_time=None,
                         lat=None, lon=None, process=True):
    """Extract peak ground motions from list of Stream objects.

    Note: The PGM columns underneath each channel will be variable
    depending on the units of the Stream being passed in (velocity
    sensors can only generate PGV) and on the imtlist passed in by
    user. Spectral acceleration columns will be formatted as SA(0.3)
    for 0.3 second spectral acceleration, for example.

    Args:
        directory (str):
            Directory of ground motion files (streams).
        imcs (list):
            Strings designating desired components to create in table.
        imts (list):
            Strings designating desired PGMs to create in table.
        epi_dist (float):
            Epicentral distance for processsing. If not included, but the lat
            and lon are, the distance will be calculated.
        event_time (float):
            Time of the event, used for processing.
        lat (float):
            Epicentral latitude. Epicentral distance calculation.
        lon (float):
            Epicentral longitude. Epicentral distance calculation.
        process (bool):
            Process the stream using the config file.

    Returns:
        DataFrame: Pandas dataframe containing columns:
            - STATION Station code.
            - NAME Text description of station.
            - LOCATION Two character location code.
            - SOURCE Long form string containing source network.
            - NETWORK Short network code.
            - LAT Station latitude
            - LON Station longitude
            - DISTANCE Epicentral distance (km) (if epicentral
              lat/lon provided)
            - HN1 East-west channel (or H1) (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
            - HN2 North-south channel (or H2) (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
            - HNZ Vertical channel (or HZ) (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
            - GREATER_OF_TWO_HORIZONTALS (multi-index with pgm columns):
                - PGA Peak ground acceleration (%g).
                - PGV Peak ground velocity (cm/s).
                - SA(0.3) Pseudo-spectral acceleration at 0.3 seconds (%g).
                - SA(1.0) Pseudo-spectral acceleration at 1.0 seconds (%g).
                - SA(3.0) Pseudo-spectral acceleration at 3.0 seconds (%g).
    """
    num_streams = len(streams)

    if imcs is None:
        station_summary_imcs = DEFAULT_IMCS
    else:
        station_summary_imcs = imcs
    if imts is None:
        station_summary_imts = DEFAULT_IMTS
    else:
        station_summary_imts = imts

    if lat is not None:
        columns = ['STATION', 'NAME', 'SOURCE',
                   'NETID', 'LAT', 'LON', 'DISTANCE']
        meta_data = np.empty((num_streams, len(columns)), dtype=list)
    else:
        columns = ['STATION', 'NAME', 'SOURCE', 'NETID', 'LAT', 'LON']
        meta_data = np.empty((num_streams, len(columns)), dtype=list)
    station_pgms = []
    imcs = []
    imts = []
    for idx, stream in enumerate(streams):
        # set meta_data
        meta_data[idx][0] = stream[0].stats['station']
        name_str = stream[0].stats['standard']['station_name']
        meta_data[idx][1] = name_str
        source = stream[0].stats.standard['source']
        meta_data[idx][2] = source
        meta_data[idx][3] = stream[0].stats['network']
        latitude = stream[0].stats['coordinates']['latitude']
        meta_data[idx][4] = latitude
        longitude = stream[0].stats['coordinates']['longitude']
        meta_data[idx][5] = longitude
        if lat is not None:
            dist, _, _ = gps2dist_azimuth(
                lat, lon, latitude, longitude)
            meta_data[idx][6] = dist/1000
            if epi_dist is None:
                epi_dist = dist/1000
        if process:
            stream = process_config(stream, event_time=event_time,
                                    epi_dist=epi_dist)
        stream_summary = StationSummary.from_stream(
            stream, station_summary_imcs, station_summary_imts)
        pgms = stream_summary.pgms
        station_pgms += [pgms]
        imcs += stream_summary.components
        imts += stream_summary.imts

    meta_columns = pd.MultiIndex.from_product([columns, ['']])
    meta_dataframe = pd.DataFrame(meta_data, columns=meta_columns)
    imcs = np.unique(imcs)
    imts = np.unique(imts)
    pgm_columns = pd.MultiIndex.from_product([imcs, imts])
    pgm_data = np.zeros((num_streams, len(imts)*len(imcs)))
    for idx, station in enumerate(station_pgms):
        subindex = 0
        for imc in imcs:
            for imt in imts:
                pgm_data[idx][subindex] = station[imt][imc]
                subindex += 1
    pgm_dataframe = pd.DataFrame(pgm_data, columns=pgm_columns)\

    dataframe = pd.concat([meta_dataframe, pgm_dataframe], axis=1)

    return dataframe


def _get_file_base(f):
    base = os.path.basename(f)
    return os.path.splitext(base)[0]


def _match_traces(trace_list):
    # Create a list of traces with matching net, sta.
    all_matches = []
    match_list = []
    for idx1, trace1 in enumerate(trace_list):
        if idx1 in all_matches:
            continue
        matches = [idx1]
        network = trace1.stats['network']
        station = trace1.stats['station']
        for idx2, trace2 in enumerate(trace_list):
            if idx1 != idx2 and idx1 not in all_matches:
                if (
                    network == trace2.stats['network'] and
                    station == trace2.stats['station']
                ):
                    matches.append(idx2)
        if len(matches) > 1:
            match_list.append(matches)
            all_matches.extend(matches)
        else:
            if matches[0] not in all_matches:
                match_list.append(matches)
                all_matches.extend(matches)
    return match_list