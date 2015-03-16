###############################################################################
#   lazyflow: data flow based lazy parallel computation framework
#
#       Copyright (C) 2011-2014, the ilastik developers
#                                <team@ilastik.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the Lesser GNU General Public License
# as published by the Free Software Foundation; either version 2.1
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# See the files LICENSE.lgpl2 and LICENSE.lgpl3 for full text of the
# GNU Lesser General Public License version 2.1 and 3 respectively.
# This information is also available on the ilastik web site at:
#		   http://ilastik.org/license/
###############################################################################

#Python
import gc
import os
import time
import threading
import weakref
import platform

import logging
logger = logging.getLogger(__name__)

#external dependencies
import psutil

#lazyflow
from lazyflow.utility import OrderedSignal, Singleton
import lazyflow

this_process = psutil.Process(os.getpid())


def memoryUsage():
    '''
    get current memory usage in bytes
    '''
    return this_process.memory_info().rss


def memoryUsagePercentage():
    '''
    get the percentage of (memory in use) / (allowed memory use)
    
    Note: the return value is obviously non-negative, but if the user specified
    memory limit is smaller than the amount of memory actually available, this
    value can be larger than 1.
    '''
    return (memoryUsage() * 100.0) / getAvailableRamBytes()


def getAvailableRamBytes():
    '''
    get the amount of memory, in bytes, that lazyflow is allowed to use
    
    Note: When a user specified setting (e.g. via .ilastikrc) is not available,
    the function will try to estimate how much memory is available after
    subtracting known overhead. Overhead estimation is currently only available
    on Mac.
    '''
    if "Darwin" in platform.system():
        # only Mac and BSD have the wired attribute, which we can use to
        # assess available RAM more precisely
        ram = psutil.virtual_memory().total - psutil.virtual_memory().wired
    else:
        ram = psutil.virtual_memory().total
    if lazyflow.AVAILABLE_RAM_MB != 0:
        # AVAILABLE_RAM_MB is the total RAM the user wants us to limit ourselves to.
        ram = min(ram, lazyflow.AVAILABLE_RAM_MB * 1024**2)
    return ram


default_refresh_interval = 5


class CacheMemoryManager(threading.Thread):
    '''
    class for the management of cache memory

    TODO: cache cleanup documentation

    Usage:
    This manager is a singleton - just call its constructor somewhere and you
    will get a reference to the *only* running memory management thread.

    Interface:
    The manager provides a signal you can subscribe to
    >>> mgr = ArrayCacheManager
    >>> mgr.totalCacheMemory.subscribe(print)
    which emits the size of all managed caches, combined, in regular intervals.

    The update interval (for the signal and for automated cache release) can
    be set with a call to a class method
    >>> ArrayCacheManager.setRefreshInterval(5)
    the interval is measured in seconds. Each change of refresh interval
    triggers cleanup.
    '''
    __metaclass__ = Singleton

    totalCacheMemory = OrderedSignal()

    loggingName = __name__ + ".ArrayCacheMemoryMgr"
    logger = logging.getLogger(loggingName)
    traceLogger = logging.getLogger("TRACE." + loggingName)

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

        self._caches = weakref.WeakSet()
        self._first_class_caches = weakref.WeakSet()
        self._observable_caches = weakref.WeakSet()
        self._managed_caches = weakref.WeakSet()

        self._condition = threading.Condition()
        self._refresh_interval = default_refresh_interval

        # maximum percentage of *allowed memory* used
        self._max_usage = 85
        # target usage percentage
        self._target_usage = 70
        self._last_usage = memoryUsagePercentage()
        self.start()

    def addFirstClassCache(self, cache):
        """
        add a first class cache (root cache) to the manager
        
        First class caches are handled differently so we are able to
        show a tree view of the caches (e.g. in ilastik). This method
        calls addCache() automatically.
        """
        # late import to prevent import loop
        from lazyflow.operators.opCache import OpCache
        if isinstance(cache, OpCache):
            self._first_class_caches.add(cache)
        self.addCache(cache)

    def getFirstClassCaches(self):
        """
        get a list of first class caches
        """
        return list(self._first_class_caches)

    def getCaches(self):
        """
        get a list of all caches (including first class caches)
        """
        return list(self._caches)

    def addCache(self, cache):
        """
        add a cache to be managed

        Caches are kept with weak references, so there is no need to
        remove them from the manager.
        """
        # late import to prevent import loop
        from lazyflow.operators.opCache import OpCache
        from lazyflow.operators.opCache import OpObservableCache
        from lazyflow.operators.opCache import OpManagedCache
        assert isinstance(cache, OpCache),\
            "Only OpCache can be managed by CacheMemoryManager"
        self._caches.add(cache)
        if isinstance(cache, OpObservableCache):
            self._observable_caches.add(cache)
        if isinstance(cache, OpManagedCache):
            self._managed_caches.add(cache)

    def run(self):
        """
        main loop
        """
        while True:
            self._wait()
            try:
                # notify subscribed functions about current cache memory
                total = 0
                for cache in self._first_class_caches:
                    total += cache.usedMemory()
                self.totalCacheMemory.emit(total)

                # check current memory state
                current_usage_percentage = memoryUsagePercentage()
                if current_usage_percentage <= self._max_usage:
                    continue

                # we need a cache cleanup
                caches = list(self._managed_caches)
                caches.sort(key=lambda x: x.lastAccessTime())
                while current_usage_percentage > self._target_usage and caches:
                    c = caches.pop(0)
                    self.logger.debug("Cleaning up cache '{}'".format(c.name))
                    c.freeMemory()
                    current_usage_percentage = memoryUsagePercentage()
                self.logger.debug(
                    "Done cleaning up, memory usage is now at "
                    "{}%".format(100*current_usage_percentage))
            except Exception as e:
                self.logger.error(str(e))

    def _wait(self):
        """
        sleep for _refresh_interval seconds or until woken up
        """
        # can't use context manager because of error messages at shutdown
        self._condition.acquire()
        self._condition.wait(self._refresh_interval)
        if self._condition is not None:
            # no idea how that happens, but it does (see above)
            self._condition.release()

    def setRefreshInterval(self, t):
        """
        set the clean up period and wake up the cleaning thread
        """
        with self._condition:
            self._refresh_interval = t
            self._condition.notifyAll()
