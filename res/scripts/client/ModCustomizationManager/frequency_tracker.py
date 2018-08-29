class FrequencyTracker:
    def __init__(self):
        self._frequency_map = {}

    def select(self, hashable):
        self._frequency_map[hashable] = self._get_frequency(hashable) + 1

    def sort_least_frequent(self, items, getter=None):
        return sorted(items, key=self._get_key_function(getter))

    def sort_most_frequent(self, items, getter=None):
        return sorted(items, reverse=True, key=self._get_key_function(getter))

    def _get_key_function(self, getter=None):
        if getter is None:
            return self._get_frequency
        return lambda item: self._get_frequency(getter(item))

    def _get_frequency(self, hashable):
        return self._frequency_map.get(hashable, 0)