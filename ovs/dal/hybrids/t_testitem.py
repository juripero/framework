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
TestDisk module
"""
from ovs.dal.dataobject import DataObject
from ovs.dal.structures import Property, Relation, Dynamic
from ovs.dal.hybrids.t_testmachine import TestMachine


class TestItem(DataObject):
    """
    This TestDisk object is used for running unittests.
    WARNING: These properties should not be changed
    """
    __properties = [Property('name', str, unique=True, doc='Name of the item'),
                    Property('description', str, mandatory=False, doc='Description of the item'),
                    Property('other_item_id', int, unique='composite_key', doc='Pointer to a other item'),
                    Property('another_item_id', int, unique='composite_key', doc='Pointer to another different item')]

    __relations = []
    __dynamics = []
