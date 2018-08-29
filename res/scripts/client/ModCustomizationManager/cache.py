import BigWorld
import cPickle
from copy import deepcopy
from os import path, makedirs
from threading import RLock


class Cache:
    def __init__(self, dir_name, file_name):
        self._cache_dir = Cache._make_dir(dir_name)
        self._file_name = file_name
        self._cache = {}
        self._write_lock = RLock()

    def get(self, namespace=None, default=None):
        namespaced_cache = self._cache.get(namespace, None)
        if namespaced_cache:
            return deepcopy(namespaced_cache)
        if not path.isfile(self._get_namespaced_file(namespace)):
            return default
        with open(self._get_namespaced_file(namespace), 'rb') as file:
            self._cache[namespace] = cPickle.loads(file.read())
            return self._cache[namespace] or default

    def set(self, cache, namespace=None):
        self._write_lock.acquire()
        self._cache[namespace] = deepcopy(cache)
        try:
            with open(self._get_namespaced_file(namespace), 'wb') as file:
                file.write(cPickle.dumps(self._cache[namespace]))
        finally:
            self._write_lock.release()

    def _get_namespaced_file(self, namespace=None):
        file_name = namespace + '.' + self._file_name if namespace is not None else self._file_name
        return path.join(self._cache_dir, file_name)

    @staticmethod
    def _make_dir(dir_name):
        wot_settings_file = unicode(BigWorld.wg_getPreferencesFilePath(), 'utf-8', errors='ignore')
        wot_settings_dir = path.dirname(wot_settings_file)
        cache_dir = path.join(wot_settings_dir, dir_name)
        if not path.isdir(cache_dir):
            makedirs(cache_dir)
        return cache_dir