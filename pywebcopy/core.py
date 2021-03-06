# -*- coding: utf-8 -*-

"""
pywebcopy.core
~~~~~~~~~~~~~~

* DO NOT TOUCH *

Core functionality of the pywebcopy engine.
"""

import os
import shutil
import zipfile
from datetime import datetime
from functools import lru_cache
from threading import enumerate, main_thread

import requests
from requests import Response
from requests.exceptions import HTTPError, ConnectionError
from six import BytesIO

from . import VERSION, SESSION, LOGGER
from .globals import MARK
from .exceptions import AccessError
from .configs import config
from .structures import RobotsTxtParser


def zip_project():
    """Makes zip archive of current project folder and returns the location.

    :rtype: str
    :returns: location of the zipped project_folder file.
    """
    _mainThread = main_thread()
    # wait for the threads to finish downloading files

    for thread in enumerate():
        if not thread or thread is _mainThread:
            continue
        if thread.is_alive():
            thread.join()

    zipf = os.path.abspath(config['project_folder']) + '.zip'

    with zipfile.ZipFile(zipf, 'w', zipfile.ZIP_DEFLATED) as archive:

        #: Iterate through file tree
        for dirn, _, fn in os.walk(config['project_folder']):
            # only files will be added to the zip archive instead of empty
            # folder which might have been created during process
            for f in fn:
                try:
                    new_fn = os.path.join(dirn, f)
                    archive.write(new_fn, new_fn[len(config['project_folder']):])
                except ValueError:
                    LOGGER.exception("Attempt to use ZIP archive that was already closed", exc_info=True)
                except RuntimeError:
                    LOGGER.exception("Failed to add file to archive file %s" % f, exc_info=True)

    LOGGER.info('Saved the Project as ZIP archive at %s' % (config['project_folder'] + '.zip'))

    # Project folder can be automatically deleted after making zip file from it
    # this is True by default and will delete the complete project folder
    if config['delete_project_folder']:
        shutil.rmtree(config['project_folder'])

    LOGGER.info("Downloaded Contents Size :: %s KB's" % str(config['download_size'] // 1024))

    return zipf


def _dummy_resp():
    """ Response with dummy data so that a dummy file will always be downloaded """

    dummy_resp = Response()
    dummy_resp.raw = BytesIO(b'This File could not be downloaded because '
                             b'the server returned an error response!')
    dummy_resp.encoding = 'utf-8'  # plain encoding
    dummy_resp.status_code = 200  # fake the status
    dummy_resp.is_dummy = True  # but leave a mark
    dummy_resp.reason = 'Failed to access'  # fail reason
    return dummy_resp


def get(url, *args, **kwargs):
    """ fetches contents from internet using `requests`.

    makes http request using custom configs
    it returns requests object if request was successful
    None otherwise.

    :param str url: the url of the page or file to be fetched
    :returns object: requests obj or None
    """

    # Make a check if url is meant for public viewing by checking for
    # the url in the robots.txt file provided by site.
    try:

        # Uses the requests module to make a get request using a persistent session
        # object and returns that
        # otherwise on fail it returns None
        resp = SESSION.get(url, *args, **kwargs)

        # log downloaded file size
        config['download_size'] += int(resp.headers.get('content-length', 0))

    except HTTPError as err:
        LOGGER.error(err)

        # try to get the default response returned by the `requests`
        resp = err.response

        if not resp:
            resp = _dummy_resp()
            resp.request = err.request

    except ConnectionError:    # Catches any other exception raised by `requests`
        LOGGER.error("Failed to access url at address %s" % url)
        resp = _dummy_resp()

    return resp


def _watermark(file_path):
    """Returns a string wrapped in comment characters for specific file type."""

    file_type = os.path.splitext(file_path)[1] or ''

    # Only specific for the html file types So that the comment does not pop up as
    # content on the page
    if file_type.lower() in ['.html', '.htm', '.xhtml', '.aspx', '.asp', '.php']:
        comment_start = '<!--!'
        comment_end = '-->'
    elif file_type.lower() in ['.css', '.js', '.xml']:
        comment_start = '/*!'
        comment_end = '*/'
    else:
        return b''

    return MARK.format(comment_start, VERSION, file_path, datetime.utcnow(), comment_end).encode()


@lru_cache(maxsize=100)
def is_allowed(ext):
    if not ext:
        return False
    if ext.strip().lower() in config['allowed_file_ext']:
        return True
    return False


def new_file(location, content_url=None, content=None):
    """Fail-safe Downloads any file to the disk.

    :param str location: path where to save the file

    :param bytes content: contents or binary data of the file
    :OR:
    :param str content_url: download the file from url

    :returns str: location of downloaded file on disk if download was successful
    None otherwise
    """
    assert location, "Download location needed to be specified!"
    assert isinstance(location, str), "Download location must be a string!"
    assert content or content_url, "Either file content or file url is needed!"
    assert isinstance(content_url, str), "File url must be a string!"

    if content:
        assert isinstance(content, bytes), "Expected type bytes, got %r instead" % type(content)

    req = None  # type: Response

    _file_ext = '.' + location.rsplit('.', 1)[1].lower().strip()

    if not is_allowed(_file_ext):
        LOGGER.critical('File ext %r is not allowed for file at %r' % (_file_ext, content_url or location))
        return

    # The file path provided can already be existing so only overwrite the files
    # when specifically configured to do so by config key 'over_write'
    if os.path.exists(location):

        if not config['over_write']:
            LOGGER.debug('File already exists at the location %s' % location)
            return location

        else:
            os.remove(location)
            LOGGER.info('ReDownloading the file of type %s to %s' % (_file_ext, location))
    else:
        LOGGER.info('Downloading a new file of type %s to %s' % (_file_ext, location))

    # Contents of the files can be supplied or filled by a content url
    # function we go online to download content from content url
    if not content and content_url is not None:

        LOGGER.info('Downloading content of file %s from %s' % (location, content_url))

        req = get(content_url, stream=True)
        # The file may not be available so will raise an error which will be caught by
        # except block an will return None
        if req is None or not req.ok:
            LOGGER.error('Failed to load the content of file %s from %s' % (location, content_url))
            return

    try:
        # Files can throw an IOError or similar when failed to open or write in that
        LOGGER.debug("Making path for the file at location %s" % location)
        if not os.path.exists(os.path.dirname(location)):
            os.makedirs(os.path.dirname(location))

    except OSError as e:
        LOGGER.critical(e)
        LOGGER.critical("Failed to create path for the file of type %s to location %s" % (_file_ext, location))
        return

    try:
        # case the function will catch it and log it then return None
        LOGGER.info("Writing file at location %s" % location)

        if isinstance(req, Response):
            with open(location, 'wb') as f:
                # should write in chunks to manage ram usages?
                f.write(req.content)
                f.write(_watermark(content_url or location))
        else:
            with open(location, 'wb') as f:
                f.write(content)
                f.write(_watermark(content_url or location))

    except Exception as e:
        LOGGER.critical(e)
        LOGGER.critical("Download failed for the file of type %s to location %s" % (_file_ext, location))
        return
    else:
        LOGGER.success('File of type %s written successfully to %s' % (_file_ext, location))
        return location
