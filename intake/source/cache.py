from datetime import datetime
from hashlib import md5
from pathlib import Path

import json
import logging
import os
import shutil

from intake.config import conf

logger = logging.getLogger('intake')

def make_caches(driver, specs):
    """
    Creates Cache objects from the cache_specs provided in the catalog yaml file.
    
    Parameters:
    -----------
    driver: str
        Name of the plugin that can load catalog entry
    specs: list
        Specification for caching the data source.
    """
    if specs is None:
        return []
    return [Cache(driver, spec) for spec in specs]

class Cache(object):
    """
    Provides utilities for managing cached data files.
    """
    blocksize = 5000000

    def __init__(self, driver, spec):
        """
        Parameters:
        -----------
        driver: str
            Name of the plugin that can load catalog entry
        spec: list
            Specification for caching the data source.
        """
        self._driver = driver
        self._spec = spec
        self._cache_dir = os.getenv('INTAKE_CACHE_DIR',
                                    conf['cache_dir'])
                             
        self._ensure_cache_dir()
        self._metadata = CacheMetadata(self._cache_dir)
    
    def _ensure_cache_dir(self):
        if not os.path.exists(self._cache_dir):
            os.makedirs(self._cache_dir)

    def _munge_path(self, cache_subdir, urlpath):
        import re
        cache_path = re.sub(
            r"%s" % self._spec['regex'],
            os.path.join(self._cache_dir, cache_subdir),
            urlpath
        )
        return cache_path

    def _hash(self, urlpath):
        return md5(
                str((os.path.basename(urlpath), 
                     self._driver)).encode()
            ).hexdigest()

    def _path(self, urlpath, subdir=None):
        if subdir is None:
            subdir = self._hash(urlpath)
        cache_path = self._munge_path(subdir, urlpath)
        dirname = os.path.dirname(cache_path)
        if not os.path.exists(dirname):
            os.makedirs(dirname)

        return cache_path

    def _log_metadata(self, urlpath, original_path, cache_path):
        metadata = {
            'created': datetime.now().isoformat(),
            'original_path': original_path,
            'cache_path': cache_path
            }
        self._metadata.update(urlpath, metadata)

    def load(self, urlpath):
        """
        Downloads data from a given url, generates a hashed filename, 
        logs metadata, and caches it locally.

        Parameters:
        ----------
        urlpath: str, location of data
            May be a local path, or remote path if including a protocol specifier
            such as ``'s3://'``. May include glob wildcards.

        Returns
        -------
        List of local cache_paths to be opened instead of the remote file(s).
        """
        from dask.bytes import open_files

        subdir = self._hash(urlpath)
        cache_paths = []
        files_in = open_files(urlpath, 'rb')
        files_out = open_files([self._path(f.path, subdir) for f in files_in], 'wb')
        for file_in, file_out in zip(files_in, files_out):
            cache_path = file_out.path
            cache_paths.append(cache_path)

            if not os.path.isfile(cache_path):
                logger.info("Caching file {} from urlpath {}".format(file_in.path, urlpath))
                self._log_metadata(urlpath, file_in.path, cache_path)

                with file_in as f1:
                    with file_out as f2:
                        data = True
                        while data:
                            #TODO: print out progress
                            data = f1.read(self.blocksize)
                            f2.write(data)
        return cache_paths

    def get_metadata(self, urlpath):
        """
        Parameters:
        ----------
        urlpath: str, location of data
            May be a local path, or remote path if including a protocol specifier
            such as ``'s3://'``. May include glob wildcards.

        Returns
        -------
        Metadata (dict) about a given urlpath.
        """
        return self._metadata[urlpath]
    
    def clear_cache(self, urlpath):
        """
        Clears cache and metadata for a given urlpath.

        Parameters:
        ----------
        urlpath: str, location of data
            May be a local path, or remote path if including a protocol specifier
            such as ``'s3://'``. May include glob wildcards.
        """
        cache_entries = self._metadata.pop(urlpath)
        for cache_entry in cache_entries:
            os.remove(cache_entry['cache_path'])
    
    def clear_all(self):
        """
        Clears all cache and metadata.
        """
        shutil.rmtree(self._cache_dir)

class CacheMetadata(object):
    """
    Utility class for managing persistent metadata stored in the cache_dir.
    """

    def __init__(self, cache_dir):
        self._cache_dir = cache_dir
        self._path = os.path.join(self._cache_dir, 'metadata.json')

        if os.path.isfile(self._path):
            with open(self._path) as f:
                self._metadata = json.load(f)
        else:
            self._metadata = {}
    
    def update(self, key, cache_entry):
        entries = self._metadata.get(key, [])
        entries.append(cache_entry)
        self._metadata[key] = entries
        self._save()

    def _save(self):
        with open(self._path, 'w') as f:
            json.dump(self._metadata, f)

    def __setitem__(self, key, item):
        self._metadata[key] = item
        self._save()
    
    def __getitem__(self, key):
        return self._metadata[key]

    def pop(self, key):
        item = self._metadata.pop(key)
        self._save()
        return item