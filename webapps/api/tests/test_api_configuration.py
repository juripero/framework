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
APIConfiguration module
"""
import os
import imp
import inspect
import unittest


class APIConfiguration(unittest.TestCase):
    """
    This test suite will validate whether all APIs are properly decorated
    """

    def test_return(self):
        """
        Validates whether all API calls have a proper @return_* decorator
        """
        functions, return_exceptions = APIConfiguration._get_functions()
        errors = []
        for fun in functions:
            fun_id = '{0}.{1}'.format(fun.__module__, fun.__name__)
            if not hasattr(fun, 'ovs_metadata'):
                errors.append('{0} - Missing metadata'.format(fun_id))
                continue
            metadata = fun.ovs_metadata
            if 'returns' not in metadata:
                errors.append('{0} - Missing @return_* decorator'.format(fun_id))
                continue
            return_metadata = metadata['returns']
            if fun_id not in return_exceptions:
                if fun.__name__ == 'list':
                    if return_metadata['returns'][0] != 'list':
                        errors.append('{0} - List return decorator expected on a list method'.format(fun_id))
                elif fun.__name__ == 'retrieve':
                    if return_metadata['returns'][0] != 'object':
                        errors.append('{0} - Object return decorator expected on a retrieve method'.format(fun_id))
                elif fun.__name__ == 'create':
                    if return_metadata['returns'][0] != 'object':
                        errors.append('{0} - Object return decorator expected on a create method'.format(fun_id))
                    if return_metadata['returns'][1] != '201':
                        errors.append('{0} - Expected status 201 on a create method'.format(fun_id))
                elif fun.__name__ == 'partial_update':
                    if return_metadata['returns'][0] != 'object':
                        errors.append('{0} - Object return decorator expected on a partial_update method'.format(fun_id))
                    if return_metadata['returns'][1] != '202':
                        errors.append('{0} - Expected status 202 on a partial_update method'.format(fun_id))
                elif fun.__name__ == 'destroy':
                    if return_metadata['returns'][0] is not None:
                        errors.append('{0} - Would not expect a return type on a destroy method'.format(fun_id))
            if return_metadata['returns'][0] is None and return_metadata['returns'][1] is None:
                if ':return:' not in fun.__doc__ or ':rtype:' not in fun.__doc__:
                    errors.append('{0} - Missing return docstring'.format(fun_id))
        self.assertEqual(len(errors), 0, 'One or more errors are found:\n- {0}'.format('\n- '.join(errors)))

    def test_load(self):
        """
        Validates whether an @load decorator is set
        """
        functions, _ = APIConfiguration._get_functions()
        errors = []
        for fun in functions:
            fun_id = '{0}.{1}'.format(fun.__module__, fun.__name__)
            if not hasattr(fun, 'ovs_metadata'):
                errors.append('{0} - Missing metadata'.format(fun_id))
                continue
            metadata = fun.ovs_metadata
            if 'load' not in metadata:
                errors.append('{0} - Missing @load decorator'.format(fun_id))
                continue
            load_metadata = metadata['load']
            parameters = load_metadata['mandatory'] + load_metadata['optional']
            missing_params = []
            for parameter in parameters:
                if ':param {0}:'.format(parameter) not in fun.__doc__:
                    missing_params.append(parameter)
                    continue
                if ':type {0}:'.format(parameter) not in fun.__doc__:
                    missing_params.append(parameter)
            if len(missing_params) > 0:
                errors.append('{0} - Missing docstring for parameters {1}'.format(fun_id, ', '.join(missing_params)))
        self.assertEqual(len(errors), 0, 'One or more errors are found:\n- {0}'.format('\n- '.join(errors)))

    @staticmethod
    def _get_functions():
        funs = []
        return_exceptions = []
        path = '/'.join([os.path.dirname(__file__), '..', 'backend', 'views'])
        for filename in os.listdir(path):
            if os.path.isfile('/'.join([path, filename])) and filename.endswith('.py'):
                name = filename.replace('.py', '')
                mod = imp.load_source(name, '/'.join([path, filename]))
                for member in inspect.getmembers(mod):
                    if inspect.isclass(member[1]) \
                            and member[1].__module__ == name \
                            and 'ViewSet' in [base.__name__ for base in member[1].__bases__]:
                        cls = member[1]
                        if hasattr(cls, 'skip_spec') and cls.skip_spec is True:
                            continue
                        if hasattr(cls, 'return_exceptions'):
                            return_exceptions += cls.return_exceptions
                        base_calls = ['list', 'retrieve', 'create', 'destroy', 'partial_update']
                        funs += [fun[1] for fun in inspect.getmembers(cls, predicate=inspect.ismethod)
                                 if fun[0] in base_calls or hasattr(fun[1], 'bind_to_methods')]
        return funs, return_exceptions
