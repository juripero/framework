# Copyright (C) 2016 iNuron NV
#
# This file is part of Open vStorage Open Source Edition (OSE),
# as available from
#
#      http://www.openvstorage.org and
#      http://www.openvstorage.com.
#
# This file is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License v3 (GNU AGPLv3)
# as published by the Free Software Foundation, in version 3 as it comes
# in the LICENSE.txt file of the Open vStorage OSE distribution.
#
# Open vStorage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY of any kind.

"""
VDisk module
"""
import time
import pickle
from ovs.dal.dataobject import DataObject
from ovs.dal.structures import Property, Relation, Dynamic
from ovs.dal.datalist import DataList
from ovs.dal.hybrids.vmachine import VMachine
from ovs.dal.hybrids.vpool import VPool
from ovs.dal.hybrids.failuredomain import FailureDomain
from ovs.extensions.storageserver.storagedriver import StorageDriverClient
from ovs.extensions.storage.volatilefactory import VolatileFactory
from ovs.log.logHandler import LogHandler


class VDisk(DataObject):
    """
    The VDisk class represents a vDisk. A vDisk is a Virtual Disk served by Open vStorage.
    vDisks can be part of a vMachine or stand-alone.
    """
    _logger = LogHandler.get('dal', name='hybrid')

    __properties = [Property('name', str, mandatory=False, doc='Name of the vDisk.'),
                    Property('description', str, mandatory=False, doc='Description of the vDisk.'),
                    Property('size', int, doc='Size of the vDisk in Bytes.'),
                    Property('devicename', str, doc='The name of the container file (e.g. the VMDK-file) describing the vDisk.'),
                    Property('order', int, mandatory=False, doc='Order with which vDisk is attached to a vMachine. None if not attached to a vMachine.'),
                    Property('volume_id', str, mandatory=False, doc='ID of the vDisk in the Open vStorage Volume Driver.'),
                    Property('parentsnapshot', str, mandatory=False, doc='Points to a parent storage driver parent ID. None if there is no parent Snapshot'),
                    Property('cinder_id', str, mandatory=False, doc='Cinder Volume ID, for volumes managed through Cinder'),
                    Property('has_manual_dtl', bool, default=False, doc='Indicates whether the default DTL location has been overruled by customer'),
                    Property('metadata', dict, default=dict(), doc='Contains fixed metadata about the volume (e.g. lba_size, ...)')]
    __relations = [Relation('vmachine', VMachine, 'vdisks', mandatory=False),
                   Relation('vpool', VPool, 'vdisks'),
                   Relation('parent_vdisk', None, 'child_vdisks', mandatory=False),
                   Relation('secondary_failure_domain', FailureDomain, 'secondary_vdisks', mandatory=False)]
    __dynamics = [Dynamic('snapshots', list, 60),
                  Dynamic('info', dict, 60),
                  Dynamic('statistics', dict, 4),
                  Dynamic('storagedriver_id', str, 60),
                  Dynamic('storagerouter_guid', str, 15),
                  Dynamic('is_vtemplate', bool, 60)]
    _fixed_properties = ['storagedriver_client']

    def __init__(self, *args, **kwargs):
        """
        Initializes a vDisk, setting up its additional helpers
        """
        DataObject.__init__(self, *args, **kwargs)
        self._frozen = False
        self._storagedriver_client = None
        self._frozen = True

    @property
    def storagedriver_client(self):
        """
        Reload Storage Driver client
        :return: StorageDriverClient
        """
        if self._storagedriver_client is None:
            self.reload_client()
        return self._storagedriver_client

    def _snapshots(self):
        """
        Fetches a list of Snapshots for the vDisk
        """
        snapshots = []
        if self.volume_id and self.vpool:
            volume_id = str(self.volume_id)
            try:
                voldrv_snapshots = self.storagedriver_client.list_snapshots(volume_id)
            except:
                voldrv_snapshots = []
            for guid in voldrv_snapshots:
                snapshot = self.storagedriver_client.info_snapshot(volume_id, guid)
                # @todo: to be investigated how to handle during set as template
                if snapshot.metadata:
                    metadata = pickle.loads(snapshot.metadata)
                    if isinstance(metadata, dict):
                        snapshots.append({'guid': guid,
                                          'timestamp': metadata['timestamp'],
                                          'label': metadata['label'],
                                          'is_consistent': metadata['is_consistent'],
                                          'is_automatic': metadata.get('is_automatic', True),
                                          'is_sticky': metadata.get('is_sticky', False),
                                          'in_backend': snapshot.in_backend,
                                          'stored': int(snapshot.stored)})
        return snapshots

    def _info(self):
        """
        Fetches the info (see Volume Driver API) for the vDisk.
        """
        if self.volume_id and self.vpool:
            try:
                vdiskinfo = self.storagedriver_client.info_volume(str(self.volume_id))
            except:
                vdiskinfo = StorageDriverClient.EMPTY_INFO()
        else:
            vdiskinfo = StorageDriverClient.EMPTY_INFO()

        vdiskinfodict = {}
        for key, value in vdiskinfo.__class__.__dict__.items():
            if type(value) is property:
                objectvalue = getattr(vdiskinfo, key)
                if key == 'object_type':
                    vdiskinfodict[key] = str(objectvalue)
                elif key == 'metadata_backend_config':
                    vdiskinfodict[key] = {}
                    if hasattr(objectvalue, 'node_configs') and callable(objectvalue.node_configs):
                        vdiskinfodict[key] = []
                        for nodeconfig in objectvalue.node_configs():
                            vdiskinfodict[key].append({'ip': nodeconfig.address(),
                                                       'port': nodeconfig.port()})
                else:
                    vdiskinfodict[key] = objectvalue
        return vdiskinfodict

    def _statistics(self, dynamic):
        """
        Fetches the Statistics for the vDisk.
        """
        statistics = {}
        for key in StorageDriverClient.STAT_KEYS:
            statistics[key] = 0
            statistics['{0}_ps'.format(key)] = 0
        for key, value in self.fetch_statistics().iteritems():
            statistics[key] += value
        statistics['timestamp'] = time.time()
        VDisk.calculate_delta(self._key, dynamic, statistics)
        return statistics

    def _storagedriver_id(self):
        """
        Returns the Volume Storage Driver ID to which the vDisk is connected.
        """
        return self.info.get('vrouter_id', None)

    def _storagerouter_guid(self):
        """
        Loads the vDisks StorageRouter guid
        """
        if not self.storagedriver_id:
            return None
        from ovs.dal.hybrids.storagedriver import StorageDriver
        sds = DataList(StorageDriver, {'type': DataList.where_operator.AND,
                                       'items': [('storagedriver_id', DataList.operator.EQUALS, self.storagedriver_id)]})
        if len(sds) == 1:
            return sds[0].storagerouter_guid
        return None

    def _is_vtemplate(self):
        """
        Returns whether the vdisk is a template
        """
        return self.info.get('object_type') == 'TEMPLATE'

    def reload_client(self):
        """
        Reloads the StorageDriver Client
        """
        if self.vpool_guid:
            self._frozen = False
            self._storagedriver_client = StorageDriverClient.load(self.vpool)
            self._frozen = True

    def fetch_statistics(self):
        """
        Loads statistics from this vDisk - returns unprocessed data
        """
        # Load data from volumedriver
        if self.volume_id and self.vpool:
            try:
                vdiskstats = self.storagedriver_client.statistics_volume(str(self.volume_id))
            except Exception as ex:
                VDisk._logger.error('Error loading statistics_volume from {0}: {1}'.format(self.volume_id, ex))
                vdiskstats = StorageDriverClient.EMPTY_STATISTICS()
        else:
            vdiskstats = StorageDriverClient.EMPTY_STATISTICS()
        # Load volumedriver data in dictionary
        vdiskstatsdict = {}
        try:
            pc = vdiskstats.performance_counters
            vdiskstatsdict['backend_data_read'] = pc.backend_read_request_size.sum()
            vdiskstatsdict['backend_data_written'] = pc.backend_write_request_size.sum()
            vdiskstatsdict['backend_read_operations'] = pc.backend_read_request_size.events()
            vdiskstatsdict['backend_write_operations'] = pc.backend_write_request_size.events()
            vdiskstatsdict['data_read'] = pc.read_request_size.sum()
            vdiskstatsdict['data_written'] = pc.write_request_size.sum()
            vdiskstatsdict['read_operations'] = pc.read_request_size.events()
            vdiskstatsdict['write_operations'] = pc.write_request_size.events()
            for key in ['cluster_cache_hits', 'cluster_cache_misses', 'metadata_store_hits',
                        'metadata_store_misses', 'sco_cache_hits', 'sco_cache_misses', 'stored']:
                vdiskstatsdict[key] = getattr(vdiskstats, key)
            # Do some more manual calculations
            block_size = self.metadata.get('lba_size', 0) * self.metadata.get('cluster_multiplier', 0)
            if block_size == 0:
                block_size = 4096
            vdiskstatsdict['4k_read_operations'] = vdiskstatsdict['data_read'] / block_size
            vdiskstatsdict['4k_write_operations'] = vdiskstatsdict['data_written'] / block_size
            # Pre-calculate sums
            for key, items in StorageDriverClient.STAT_SUMS.iteritems():
                vdiskstatsdict[key] = 0
                for item in items:
                    vdiskstatsdict[key] += vdiskstatsdict[item]
        except:
            pass
        return vdiskstatsdict

    @staticmethod
    def calculate_delta(key, dynamic, current_stats):
        """
        Calculate statistics deltas
        :param key: Key to retrieve from volatile factory
        :param dynamic:
        :param current_stats: Current statistics to compare with
        :return: None
        """
        volatile = VolatileFactory.get_client()
        prev_key = '{0}_{1}'.format(key, 'statistics_previous')
        previous_stats = volatile.get(prev_key, default={})
        for key in current_stats.keys():
            if key in StorageDriverClient.STAT_KEYS:
                delta = current_stats['timestamp'] - previous_stats.get('timestamp', current_stats['timestamp'])
                if delta < 0:
                    current_stats['{0}_ps'.format(key)] = 0
                elif delta == 0:
                    current_stats['{0}_ps'.format(key)] = previous_stats.get('{0}_ps'.format(key), 0)
                else:
                    current_stats['{0}_ps'.format(key)] = max(0, (current_stats[key] - previous_stats[key]) / delta)
        volatile.set(prev_key, current_stats, dynamic.timeout * 10)
