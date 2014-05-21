# Copyright 2014 CloudFounders NV
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Module for SetupController
"""

import os
import re
import sys
import time
import ConfigParser
import urllib2
import base64
from string import digits

from ovs.extensions.generic.sshclient import SSHClient
from ovs.extensions.generic.interactive import Interactive
from ovs.extensions.db.arakoon.ArakoonManagement import ArakoonManagement
from ovs.log.logHandler import LogHandler

logger = LogHandler('lib', name='setup')

# @TODO: Add logging everywhere
# @TODO: Make the setup_node idempotent
# @TODO: Make it possible to run as a non-privileged user


class SetupController(object):
    """
    This class contains all logic for setting up an environment, installed with system-native packages
    """

    @staticmethod
    def setup_node(ip=None, verbose=False):
        """
        Sets up a node, post installation
        """

        default_hypervisor_username = 'root'

        try:
            print Interactive.boxed_message(['Open vStorage Setup'])

            # Prepare variables
            target_password = None
            join_cluster = False
            cluster_name = None
            cluster_ip = ''
            master_ip = None
            master_password = None
            nodes = []
            extra_hdd_storage = ''
            extra_hdd_mountpoint = ''
            use_hdd_io_ssd = ''
            ssd_mountpoint = ''
            hypervisor_type = ''
            hypervisor_name = ''
            hypervisor_ip = ''
            hypervisor_username = ''
            hypervisor_password = ''
            arakoon_mountpoint = ''
            # @TODO: node password identical for all nodes
            #       to be extended if different password need to be supported / node

            rabbitmq_server_config = """
[
   {rabbit, [{tcp_listeners, [%(broker_port)s]},
             {default_user, <<"%(broker_username)s">>},
             {default_pass, <<"%(broker_password)s">>},
             {cluster_nodes, {[], disc}}]}
].
"""
            # Support non-interactive setup
            preconfig = '/tmp/openvstorage_preconfig.cfg'
            if os.path.exists(preconfig):
                config = ConfigParser.ConfigParser()
                config.read(preconfig)
                ip = config.get('setup', 'target_ip')
                target_password = config.get('setup', 'target_password')
                cluster_ip = config.get('setup', 'cluster_ip')
                cluster_name = str(config.get('setup', 'cluster_name'))
                join_cluster = config.getboolean('setup', 'join_cluster')
                master_ip = config.get('setup', 'master_ip')
                master_password = config.get('setup', 'master_password')
                extra_hdd_storage = config.getboolean('setup', 'extra_hdd_storage')
                extra_hdd_mountpoint = config.get('setup', 'extra_hdd_mountpoint')
                use_hdd_io_ssd = config.get('setup', 'use_hdd_io_ssd')
                ssd_mountpoint = config.get('setup', 'ssd_mountpoint')
                hypervisor_type = config.get('setup', 'hypervisor_type')
                hypervisor_name = config.get('setup', 'hypervisor_name')
                hypervisor_ip = config.get('setup', 'hypervisor_ip')
                hypervisor_username = config.get('setup', 'hypervisor_username')
                hypervisor_password = config.get('setup', 'hypervisor_password')
                arakoon_mountpoint = config.get('setup', 'arakoon_mountpoint')
                verbose = config.getboolean('setup', 'verbose')

            # Create connection to target node
            print '\n+++ Setting up connections +++\n'
            if ip is None:
                ip = '127.0.0.1'
            if target_password is None:
                node_string = 'this node' if ip == '127.0.0.1' else ip
                target_node_password = Interactive.ask_password('Enter the root password for {0}'.format(node_string))
            else:
                target_node_password = target_password
            target_client = SSHClient.load(ip, target_node_password)
            if verbose:
                from ovs.plugin.provider.remote import Remote
                Remote.cuisine.fabric.output['running'] = True

            # Collect information about the cluster to join
            print '\n+++ Collecting cluster information +++\n'
            current_cluster_names = []
            clusters = []
            discovery_result = SetupController._discover_nodes(target_client)
            if discovery_result:
                clusters = discovery_result.keys()
                current_cluster_names = clusters[:]
            else:
                print 'No existing Open vStorage clusters are found.'

            node_name = target_client.run('hostname')
            if cluster_name is None:
                if len(clusters) > 0:
                    dont_join = "Don't join any of these clusters."
                    clusters.append(dont_join)
                    print 'Following Open vStorage clusters are found.'
                    cluster_name = Interactive.ask_choice(clusters, 'Select a cluster to join')
                    if cluster_name != dont_join:
                        cluster_nodes = discovery_result[cluster_name].keys()
                        if node_name in cluster_nodes:
                            continue_install = Interactive.ask_yesno(
                                '{0} already exists in cluster {1}. Do you want to continue?'.format(node_name, cluster_name),
                                default_value=True
                            )
                            if continue_install is False:
                                raise ValueError('Duplicate node name found.')
                        nodes = discovery_result[cluster_name].values()
                        master_name = Interactive.ask_choice(cluster_nodes, 'Select your master node')
                        master_ip = discovery_result[cluster_name][master_name]
                        join_cluster = True
                    else:
                        cluster_name = None

                if join_cluster is False and cluster_name is None:
                    while True:
                        cluster_name = Interactive.ask_string('Please enter the cluster name')
                        if cluster_name in current_cluster_names:
                            print 'The new cluster name should be unique.'
                        elif '_' in cluster_name:
                            print "The new cluster name should not contain '_'."
                        else:
                            break

            else:  # Automated install
                if cluster_name in discovery_result:
                    nodes = discovery_result[cluster_name].values()

            # Creating filesystems
            print '\n+++ Creating filesystems +++\n'
            if extra_hdd_storage == '' or extra_hdd_storage is None:
                extra_hdd_storage = Interactive.ask_yesno('Do you want to configure an additional HDD for data storage?', default_value=False)
#             else:
#                 extra_hdd_storage = extra_hdd_storage
            SetupController._create_filesystems(target_client, extra_hdd_storage, extra_hdd_mountpoint, use_hdd_io_ssd, ssd_mountpoint)

            # Get target grid ip
            print '\n+++ Collecting generic information +++\n'
            ovs_config = SetupController._remote_config_read(target_client, '/opt/OpenvStorage/config/ovs.cfg')
            ipaddresses = target_client.run(
                "ip a | grep 'inet ' | sed 's/\s\s*/ /g' | cut -d ' ' -f 3 | cut -d '/' -f 1"
            ).strip().split('\n')
            ipaddresses = [found_ip.strip() for found_ip in ipaddresses if found_ip.strip() != '127.0.0.1']
            if not cluster_ip:
                cluster_ip = Interactive.ask_choice(ipaddresses, 'Select the public ip address of {0}'.format(node_name))
            if cluster_ip not in nodes:
                nodes.append(cluster_ip)
            # Collecting hypervisor data
            possible_hypervisor = SetupController._discover_hypervisor(target_client)
            if not hypervisor_type:
                hypervisor_type = Interactive.ask_choice(['VMWARE', 'KVM'],
                                                    question='Which type of hypervisor is this Grid Storage Router (GSR) backing?',
                                                    default_value=possible_hypervisor)
            default_name = 'esxi' if hypervisor_type == 'VMWARE' else 'kvm'
            if not hypervisor_name:
                hypervisor_name = Interactive.ask_string('Enter hypervisor hostname', default_value=default_name)
            if hypervisor_type == 'VMWARE':
                first_request = True  # If parameters are wrong, we need to re-ask it
                while True:
                    if not hypervisor_ip or not first_request:
                        hypervisor_ip = Interactive.ask_string('Enter hypervisor ip address', default_value=hypervisor_ip)
                    if not hypervisor_username or not first_request:
                        hypervisor_username = Interactive.ask_string('Enter hypervisor username', default_value=default_hypervisor_username)
                    if not hypervisor_password or not first_request:
                        hypervisor_password = Interactive.ask_password('Enter hypervisor root password')
                    try:
                        request = urllib2.Request('https://{0}/mob'.format(hypervisor_ip))
                        auth = base64.encodestring('{0}:{1}'.format(hypervisor_username, hypervisor_password)).replace('\n', '')
                        request.add_header("Authorization", "Basic %s" % auth)
                        urllib2.urlopen(request).read()
                        break
                    except Exception as ex:
                        first_request = False
                        print 'Could not connect to {0}: {1}'.format(hypervisor_ip, ex)
            elif hypervisor_type == 'KVM':
                # In case of KVM, the VSA is the pMachine, so credentials are shared.
                hypervisor_ip = cluster_ip
                hypervisor_username = 'root'
                hypervisor_password = target_node_password

            # Ask for Arakoon's db location
            mountpoints = target_client.run('mount -v').strip().split('\n')
            mountpoints = [p.split(' ')[2] for p in mountpoints if
                           len(p.split(' ')) > 2 and ('/mnt/' in p.split(' ')[2] or '/var' in p.split(' ')[2])]
            if not arakoon_mountpoint:
                arakoon_mountpoint = Interactive.ask_choice(mountpoints, question='Select arakoon database mountpoint',
                                                            default_value=Interactive.find_in_list(mountpoints, 'db'))
            mountpoints.remove(arakoon_mountpoint)

            print '\n+++ Adding basic configuration +++\n'
            # Elastic search setup
            print 'Configuring elastic search'
            SetupController._add_service(target_client, 'elasticsearch')
            config_file = '/etc/elasticsearch/elasticsearch.yml'
            SetupController._change_service_state(target_client, 'elasticsearch', 'stop')
            target_client.run('cp /opt/OpenvStorage/config/elasticsearch.yml /etc/elasticsearch/')
            target_client.run('mkdir -p /opt/data/elasticsearch/work')
            target_client.run('chown -R elasticsearch:elasticsearch /opt/data/elasticsearch*')
            SetupController._replace_param_in_config(target_client,
                                                     config_file,
                                                     '<CLUSTER_NAME>',
                                                     'ovses_{0}'.format(cluster_name),
                                                     add=False)
            SetupController._replace_param_in_config(target_client,
                                                     config_file,
                                                     '<NODE_NAME>',
                                                     node_name)
            SetupController._replace_param_in_config(target_client,
                                                     config_file,
                                                     '<NETWORK_PUBLISH>',
                                                     cluster_ip)
            SetupController._change_service_state(target_client, 'elasticsearch', 'start')
            SetupController._replace_param_in_config(target_client,
                                                     '/etc/logstash/conf.d/indexer.conf',
                                                     '<CLUSTER_NAME>',
                                                     'ovses_{0}'.format(cluster_name))
            SetupController._change_service_state(target_client, 'logstash', 'restart')

            # Exchange ssh keys
            print 'Exchanging SSH keys'
            passwords = None
            prev_node_password = ''
            for node in nodes:
                if passwords is None:
                    if not target_password:
                        prev_node_password = Interactive.ask_password('Enter root password for {0}'.format(node))
                    else:
                        prev_node_password = target_password
                    passwords = {node: prev_node_password}
                else:
                    if not target_password:
                        this_node_password = Interactive.ask_password(
                            'Enter root password for {0}, just press enter if identical as above'.format(node)
                        )
                        if this_node_password == '':
                            this_node_password = prev_node_password
                    else:
                        this_node_password = target_password
                    passwords[node] = this_node_password
                    prev_node_password = this_node_password
            root_ssh_folder = '/root/.ssh'
            ovs_ssh_folder = '/opt/OpenvStorage/.ssh'
            public_key_filename = '{0}/id_rsa.pub'
            authorized_keys_filename = '{0}/authorized_keys'
            known_hosts_filename = '{0}/known_hosts'
            authorized_keys = ''
            for node in nodes:
                node_client = SSHClient.load(node, passwords[node])
                root_pub_key = node_client.file_read(public_key_filename.format(root_ssh_folder))
                ovs_pub_key = node_client.file_read(public_key_filename.format(ovs_ssh_folder))
                authorized_keys += '{0}\n{1}\n'.format(root_pub_key, ovs_pub_key)
            for node in nodes:
                node_client = SSHClient.load(node, passwords[node])
                node_client.file_write(authorized_keys_filename.format(root_ssh_folder), authorized_keys)
                node_client.file_write(authorized_keys_filename.format(ovs_ssh_folder), authorized_keys)
                node_client.run(
                    'ssh-keyscan -H {0} >> {1}'.format(' '.join(nodes), known_hosts_filename.format(root_ssh_folder))
                )
                node_client.run(
                    'su - ovs -c "ssh-keyscan -H {0} >> {1}"'.format(' '.join(nodes), known_hosts_filename.format(ovs_ssh_folder))
                )

            # Define services
            model_services = ['arakoon-ovsdb', 'memcached', 'arakoon-voldrv']
            master_services = ['rabbitmq', 'scheduled-tasks', 'snmp']
            extra_services = ['webapp-api', 'nginx', 'workers', 'volumerouter-consumer']
            all_services = model_services + master_services + extra_services

            arakoon_client_config = '/opt/OpenvStorage/config/arakoon/{0}/{0}_client.cfg'
            arakoon_server_config = '/opt/OpenvStorage/config/arakoon/{0}/{0}.cfg'
            arakoon_local_nodes = '/opt/OpenvStorage/config/arakoon/{0}/{0}_local_nodes.cfg'
            arakoon_clusters = {'ovsdb': 8870, 'voldrv': 8872}
            generic_configfiles = {'/opt/OpenvStorage/config/memcacheclient.cfg': 11211,
                                   '/opt/OpenvStorage/config/rabbitmqclient.cfg': 5672}

            # Setting up Arakoon
            print 'Setting up persistent datastore'
            unique_id = ovs_config.get('core', 'uniqueid')
            join_masters = False
            if join_cluster:
                # Updating configuration files and copy them around
                for cluster in arakoon_clusters.keys():
                    master_client = SSHClient.load(master_ip, master_password)
                    config = SetupController._remote_config_read(master_client, arakoon_client_config.format(cluster))
                    cluster_nodes = [node.strip() for node in config.get('global', 'cluster').split(',')]
                    if len(cluster_nodes) < 3:
                        join_masters = True
            else:
                join_masters = True

            print 'Adding services'
            target_client = SSHClient.load(cluster_ip)
            params = {'<ARAKOON_NODE_ID>' : unique_id,
                      '<MEMCACHE_NODE_IP>': cluster_ip,
                      '<WORKER_QUEUE>': unique_id}
            for service in all_services:
                SetupController._add_service(target_client, service, params)

            if join_masters:
                print '\n+++ Joining master node +++\n'

                print 'Stopping services'
                for node in nodes:
                    node_client = SSHClient.load(node)
                    for service in all_services:
                        SetupController._change_service_state(node_client, service, 'stop')

                if join_cluster:
                    print 'Joining arakoon cluster'
                    for cluster in arakoon_clusters.keys():
                        master_client = SSHClient.load(master_ip, master_password)
                        client_config = SetupController._remote_config_read(master_client, arakoon_client_config.format(cluster))
                        server_config = SetupController._remote_config_read(master_client, arakoon_server_config.format(cluster))
                        for node in nodes:
                            node_client = SSHClient.load(node)
                            node_client.dir_ensure('/opt/OpenvStorage/config/arakoon/{0}'.format(cluster), True)
                            SetupController._configure_arakoon((client_config, server_config), unique_id, cluster, cluster_ip,
                                                               arakoon_clusters[cluster], node_client,
                                                               (arakoon_client_config, arakoon_server_config, arakoon_local_nodes),
                                                               arakoon_mountpoint)
                else:
                    print 'Setting up first arakoon node'
                    target_client = SSHClient.load(ip)
                    target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/ovsdb', True)
                    target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/voldrv', True)
                    for cluster in arakoon_clusters.keys():
                        client_config = ConfigParser.ConfigParser()
                        server_config = ConfigParser.ConfigParser()
                        SetupController._configure_arakoon((client_config, server_config), unique_id, cluster,
                                                           cluster_ip,
                                                           arakoon_clusters[cluster], target_client,
                                                           (arakoon_client_config, arakoon_server_config, arakoon_local_nodes),
                                                           arakoon_mountpoint)

                target_client = SSHClient.load(ip)
                for cluster in arakoon_clusters.keys():
                    arakoon_create_directories = """
from ovs.extensions.db.arakoon.ArakoonManagement import ArakoonManagement
arakoon_management = ArakoonManagement()
arakoon_cluster = arakoon_management.getCluster('%(cluster)s')
arakoon_cluster.createDirs(arakoon_cluster.listLocalNodes()[0])
""" % {'cluster': cluster}
                    SetupController._exec_python(target_client, arakoon_create_directories)

                print 'Updating hosts files'
                for node in nodes:
                    client_node = SSHClient.load(node)
                    update_hosts_file = """
from ovs.extensions.generic.system import Ovs
Ovs.update_hosts_file(hostname='%(host)s', ip='%(ip)s')
""" % {'ip': cluster_ip,
       'host': node_name}
                    SetupController._exec_python(client_node, update_hosts_file)
                    if node != ip:
                        SetupController._change_service_state(client_node, 'rabbitmq', 'start')
                    else:
                        for subnode in nodes:
                            client_node = SSHClient.load(subnode)
                            node_hostname = client_node.run('hostname')
                            update_hosts_file = """
from ovs.extensions.generic.system import Ovs
Ovs.update_hosts_file(hostname='%(host)s', ip='%(ip)s')
""" % {'ip': subnode,
       'host': node_hostname}
                            client = SSHClient.load(ip)
                            SetupController._exec_python(client, update_hosts_file)

                print 'Setting up RabbitMQ'
                client = SSHClient.load(ip)

                client.run("""cat > /etc/rabbitmq/rabbitmq.config << EOF
{0}
EOF
""".format(rabbitmq_server_config % {'broker_port' : ovs_config.get('core', 'broker.port'),
       'broker_username' : ovs_config.get('core', 'broker.login'),
       'broker_password' : ovs_config.get('core', 'broker.password')}))

                client.run('rabbitmq-server -detached; sleep 5; rabbitmqctl stop_app; sleep 5; rabbitmqctl reset; sleep 5; rabbitmqctl stop; sleep 5;')

                if join_masters and join_cluster:
                    # Copy rabbitmq cookie
                    rabbitmq_cookie_file = '/var/lib/rabbitmq/.erlang.cookie'
                    master_client = SSHClient.load(master_ip)
                    contents = master_client.file_read(rabbitmq_cookie_file)
                    master_hostname = master_client.run('hostname')
                    client = SSHClient.load(ip)
                    client.dir_ensure(os.path.dirname(rabbitmq_cookie_file), True)
                    client.file_write(rabbitmq_cookie_file, contents)
                    client.file_attribs(rabbitmq_cookie_file, mode=400)
                    client.run('rabbitmq-server -detached; sleep 5; rabbitmqctl stop_app; sleep 5;')
                    client.run('rabbitmqctl join_cluster rabbit@{}; sleep 5;'.format(master_hostname))
                    client.run('rabbitmqctl stop; sleep 5;')

                if join_cluster:
                    print 'Distribute configuration files'
                    for config_file, port in generic_configfiles.iteritems():
                        master_client = SSHClient.load(master_ip)
                        config = SetupController._remote_config_read(master_client, config_file)
                        config_nodes = [n.strip() for n in config.get('main', 'nodes').split(',')]
                        if unique_id not in config_nodes:
                            config.set('main', 'nodes', ', '.join(config_nodes + [unique_id]))
                            config.add_section(unique_id)
                            config.set(unique_id, 'location', '{0}:{1}'.format(cluster_ip, port))
                        for node in nodes:
                            node_client = SSHClient.load(node)
                            SetupController._remote_config_write(node_client, config_file, config)
                else:
                    print 'Build configuration files'
                    for config_file, port in generic_configfiles.iteritems():
                        config = ConfigParser.ConfigParser()
                        config.add_section('main')
                        config.set('main', 'nodes', unique_id)
                        config.add_section(unique_id)
                        config.set(unique_id, 'location', '{0}:{1}'.format(cluster_ip, port))
                        client = SSHClient.load(ip)
                        SetupController._remote_config_write(client, config_file, config)

                print 'Update existing vPools'
                for node in nodes:
                    client_node = SSHClient.load(node)
                    update_voldrv = """
import os
from ovs.plugin.provider.configuration import Configuration
from ovs.extensions.storageserver.volumestoragerouter import VolumeStorageRouterConfiguration
from ovs.extensions.db.arakoon.ArakoonManagement import ArakoonManagement
arakoon_management = ArakoonManagement()
voldrv_arakoon_cluster_id = 'voldrv'
voldrv_arakoon_cluster = arakoon_management.getCluster(voldrv_arakoon_cluster_id)
voldrv_arakoon_client_config = voldrv_arakoon_cluster.getClientConfig()
configuration_dir = Configuration.get('ovs.core.cfgdir')
if not os.path.exists('{0}/voldrv_vpools'.format(configuration_dir)):
    os.makedirs('{0}/voldrv_vpools'.format(configuration_dir))
for json_file in os.listdir('{0}/voldrv_vpools'.format(configuration_dir)):
    if json_file.endswith('.json'):
        vsr_config = VolumeStorageRouterConfiguration(json_file.replace('.json', ''))
        vsr_config.configure_arakoon_cluster(voldrv_arakoon_cluster_id, voldrv_arakoon_client_config)
"""
                    SetupController._exec_python(client_node, update_voldrv)

                for node in nodes:
                    node_client = SSHClient.load(node)
                    SetupController._configure_amqp_to_volumedriver(node_client)

                print 'Starting services'
                for node in nodes:
                    node_client = SSHClient.load(node)
                    for service in model_services:
                        SetupController._change_service_state(node_client, service, 'start')

                if not join_cluster:
                    print 'Start model migration'
                    from ovs.extensions.migration.migration import Migration
                    Migration.migrate()
                client = SSHClient.load(ip)
                SetupController._update_es_configuration(client, 'true')

            else:
                print '\n+++ Adding extra node +++\n'
                for cluster in arakoon_clusters.keys():
                    master_client = SSHClient.load(master_ip)
                    client_config = SetupController._remote_config_read(master_client, arakoon_client_config.format(cluster))
                    target_client = SSHClient.load(ip)
                    target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/ovsdb', True)
                    target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/voldrv', True)
                    SetupController._remote_config_write(target_client, arakoon_client_config.format(cluster), client_config)

                print 'Stopping services'
                client = SSHClient.load(ip)
                # Disable master and model services
                for service in master_services + model_services:
                    SetupController._disable_service(client, service)
                # Stop services
                for service in all_services:
                    SetupController._change_service_state(client, service, 'stop')

                print 'Configuring services'
                for config in generic_configfiles.keys():
                    master_client = SSHClient.load(master_ip)
                    client_config = SetupController._remote_config_read(master_client, config)
                    target_client = SSHClient.load(ip)
                    SetupController._remote_config_write(target_client, config, client_config)

                client = SSHClient.load(ip)
                SetupController._update_es_configuration(client, 'false')

            print '\n+++ Finalizing setup +++\n'
            client = SSHClient.load(ip)
            client.run('mkdir -p /opt/OpenvStorage/webapps/frontend/logging')
            SetupController._change_service_state(client, 'logstash', 'restart')
            SetupController._replace_param_in_config(client,
                                                     '/opt/OpenvStorage/webapps/frontend/logging/config.js',
                                                     'http://"+window.location.hostname+":9200',
                                                     'http://' + cluster_ip + ':9200')

            arakoon_management = ArakoonManagement()
            counter = 0
            for cluster in arakoon_clusters.keys():
                master_elected = False
                while not master_elected:
                    client = arakoon_management.getCluster(cluster).getClient()
                    try:
                        client.whoMaster()
                        master_elected = True
                    except:
                        print "Arakoon master not yet determined for {0}".format(cluster)
                        counter = 1 if counter == 0 else (counter * 2)
                        if counter > 10:
                            raise RuntimeError('Arakoon could not be started')
                        time.sleep(counter)

            # Imports, not earlier then here, as all required config files should be in place.
            from ovs.dal.hybrids.pmachine import PMachine
            from ovs.dal.lists.pmachinelist import PMachineList
            from ovs.dal.hybrids.vmachine import VMachine
            from ovs.dal.lists.vmachinelist import VMachineList

            print 'Configuring/updating model'
            pmachine = None
            for current_pmachine in PMachineList.get_pmachines():
                if current_pmachine.ip == hypervisor_ip and current_pmachine.hvtype == hypervisor_type:
                    pmachine = current_pmachine
                    break
            if pmachine is None:
                pmachine = PMachine()
                pmachine.ip = hypervisor_ip
                pmachine.username = hypervisor_username
                pmachine.password = hypervisor_password
                pmachine.hvtype = hypervisor_type
                pmachine.name = hypervisor_name
                pmachine.save()
            vsa = None
            for current_vsa in VMachineList.get_vsas():
                if current_vsa.ip == ip and current_vsa.machineid == unique_id:
                    vsa = current_vsa
                    break
            if vsa is None:
                vsa = VMachine()
                vsa.name = node_name
                vsa.is_vtemplate = False
                vsa.is_internal = True
                vsa.machineid = unique_id
                vsa.ip = cluster_ip
                vsa.save()
            vsa.pmachine = pmachine
            vsa.save()

            print 'Updating configuration files'
            ovs_config.set('grid', 'ip', cluster_ip)
            target_client = SSHClient.load(ip)
            SetupController._remote_config_write(target_client, '/opt/OpenvStorage/config/ovs.cfg', ovs_config)

            some_services = [s for s in extra_services if s != 'workers']
            print 'Starting services'
            if join_masters is True:
                for node in nodes:
                    node_client = SSHClient.load(node)
                    for service in master_services + some_services:
                        SetupController._change_service_state(node_client, service, 'start')
                # Enable HA for the rabbitMQ queues
                client = SSHClient.load(ip)
                client.run('sleep 5;rabbitmqctl set_policy ha-all "^(volumerouter|ovs_.*)$" \'{"ha-mode":"all"}\'')
            else:
                for node in nodes:
                    node_client = SSHClient.load(node)
                    for service in some_services:
                        SetupController._change_service_state(node_client, service, 'start')

            for node in nodes:
                node_client = SSHClient.load(node)
                SetupController._change_service_state(node_client, 'workers', 'restart')

            print '\n+++ Announcing service +++\n'
            target_client = SSHClient.load(ip)
            target_client.run("""cat > /etc/avahi/services/ovs_cluster.service <<EOF
<?xml version="1.0" standalone='no'?>
<!--*-nxml-*-->
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- $Id$ -->
<service-group>
    <name replace-wildcards="yes">ovs_cluster_{0}_{1}</name>
    <service>
        <type>_ovs_cluster_{0}_{1}._tcp</type>
        <port>443</port>
    </service>
</service-group>
EOF
""".format(cluster_name, node_name))
            SetupController._change_service_state(target_client, 'avahi-daemon', 'restart')

            if join_cluster:
                print Interactive.boxed_message(['Setup complete.',
                                                 'Point your browser to http://{0} to use Open vStorage'.format(master_ip)])
            else:
                print Interactive.boxed_message(['Setup complete.',
                                                 'Point your browser to http://{0} to start using Open vStorage'.format(cluster_ip)])

        except Exception as exception:
            print ''  # Spacing
            print Interactive.boxed_message(['An unexpected error occurred:', str(exception)])
            sys.exit(1)

    @staticmethod
    def _add_service(client, name, params=None):
        if params is None:
            params = {}
        run_service_script = """
from ovs.plugin.provider.service import Service
Service.add_service('', '{0}', '', '', {1})
"""
#        Service.add_service('', name, '', '', params)
        _ = SetupController._exec_python(client,
                                         run_service_script.format(name, params))

    @staticmethod
    def _remove_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
Service.remove_service('', '{0}')
"""
#        Service.remove_service('', name, '', '')
        _ = SetupController._exec_python(client,
                                         run_service_script.format(name))

    @staticmethod
    def _disable_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
Service.disable_service('{0}')
"""
        _ = SetupController._exec_python(client,
                                         run_service_script.format(name))

    @staticmethod
    def _enable_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
Service.enable_service('{0}')
"""
        _ = SetupController._exec_python(client,
                                         run_service_script.format(name))

    @staticmethod
    def _get_service_status(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
print Service.get_service_status('{0}')
"""
        status = SetupController._exec_python(client,
                                              run_service_script.format(name))
        if status == 'True':
            return True
        if status == 'False':
            return False
        return None

    @staticmethod
    def _restart_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
print Service.restart_service('{0}')
"""
        status = SetupController._exec_python(client,
                                              run_service_script.format(name))
        return status

    @staticmethod
    def _start_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
print Service.start_service('{0}')
"""
        status = SetupController._exec_python(client,
                                              run_service_script.format(name))
        return status

    @staticmethod
    def _stop_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
print Service.stop_service('{0}')
"""
        status = SetupController._exec_python(client,
                                              run_service_script.format(name))
        return status

    @staticmethod
    def _configure_arakoon(configs, unique_id, cluster, ip, port, target_client, arakoon_configs, arakoon_mountpoint):
        arakoon_client_config, arakoon_server_config, arakoon_local_nodes = arakoon_configs
        client_config, server_config = configs

        if not client_config.has_section('global'):
            client_config.add_section('global')
        client_config.set('global', 'cluster_id', cluster)
        current_cluster = list()
        if client_config.has_option('global', 'cluster'):
            current_cluster = [n.strip() for n in client_config.get('global', 'cluster').split(',')]
        if unique_id not in current_cluster:
            current_cluster.append(unique_id)
        client_config.set('global', 'cluster', ', '.join(current_cluster))
        if not client_config.has_section(unique_id):
            client_config.add_section(unique_id)
            client_config.set(unique_id, 'name', unique_id)
            client_config.set(unique_id, 'ip', ip)
            client_config.set(unique_id, 'client_port', port)
        SetupController._remote_config_write(target_client, arakoon_client_config.format(cluster), client_config)

        if not server_config.has_section('global'):
            server_config.add_section('global')
        server_config.set('global', 'cluster_id', cluster)
        current_cluster = list()
        if server_config.has_option('global', 'cluster'):
            current_cluster = [n.strip() for n in server_config.get('global', 'cluster').split(',')]
        if unique_id not in current_cluster:
            current_cluster.append(unique_id)
        server_config.set('global', 'cluster', ', '.join(current_cluster))
        if not server_config.has_section(unique_id):
            server_config.add_section(unique_id)
            server_config.set(unique_id, 'name', unique_id)
            server_config.set(unique_id, 'ip', ip)
            server_config.set(unique_id, 'client_port', port)
            server_config.set(unique_id, 'messaging_port', port + 1)
            server_config.set(unique_id, 'log_level', 'info')
            server_config.set(unique_id, 'log_dir', '/var/log/arakoon/{0}'.format(cluster))
            server_config.set(unique_id, 'home', '{0}/arakoon/{1}'.format(arakoon_mountpoint, cluster))
            server_config.set(unique_id, 'tlog_dir', '{0}/tlogs/{1}'.format(arakoon_mountpoint, cluster))
            server_config.set(unique_id, 'fsync', 'true')
        SetupController._remote_config_write(target_client, arakoon_server_config.format(cluster), server_config)

        local_config = ConfigParser.ConfigParser()
        local_config.add_section('global')
        local_config.set('global', 'cluster', unique_id)
        SetupController._remote_config_write(target_client, arakoon_local_nodes.format(cluster), local_config)

    @staticmethod
    def _create_filesystems(fs_client, create_extra, extra_hdd_mountpoint, use_hdd_io_ssd, ssd_mountpoint):
        """
        Creates filesystems on the first two additional disks
        """
        # Scan block devices
        drive_lines = fs_client.run(
            "ls -l /dev/* | grep -E '/dev/(sd..?|fio..?)' | sed 's/\s\s*/ /g' | cut -d ' ' -f 10"
        ).strip().split('\n')
        drives = {}
        for drive in drive_lines:
            partition = drive.strip()
            if partition == '':
                continue
            drive = partition.translate(None, digits)
            if '/dev/sd' in drive:
                if drive not in drives:
                    identifier = drive.replace('/dev/', '')
                    if fs_client.run('cat /sys/block/{0}/device/type'.format(identifier)).strip() == '0' \
                            and fs_client.run('cat /sys/block/{0}/removable'.format(identifier)).strip() == '0':
                        ssd_output = fs_client.run(
                            "/usr/bin/lsscsi | grep 'FUSIONIO' | grep {0} || true".format(drive)
                        ).strip()
                        ssd_output += str(fs_client.run(
                            "hdparm -I {0} 2> /dev/null | grep 'Solid State' || true".format(drive)
                        ).strip())
                        drives[drive] = {'ssd': ('Solid State' in ssd_output or 'FUSIONIO' in ssd_output),
                                         'partitions': []}
                if drive in drives:
                    drives[drive]['partitions'].append(partition)
            else:
                if drive not in drives:
                    drives[drive] = {'ssd': True, 'partitions': []}
                drives[drive]['partitions'].append(partition)
        mounted = [device.strip() for device in fs_client.run("mount | cut -d ' ' -f 1").strip().split('\n')]
        root_partition = fs_client.run("mount | grep 'on / ' | cut -d ' ' -f 1").strip()
        # Start preparing partitions
        extra_mountpoints = ''
        hdds = [drive for drive, info in drives.iteritems() if
                info['ssd'] is False and root_partition not in info['partitions']]
        if create_extra:
            # Create partitions on HDD
            if len(hdds) == 0:
                raise Exception('No HDD was found. At least one HDD is required when creating extra filesystems')
            if len(hdds) > 1:
                if not extra_hdd_mountpoint:
                    hdd = Interactive.ask_choice(hdds, question='Choose the HDD to use for Open vStorage')
                else:
                    hdd = extra_hdd_mountpoint
            else:
                hdd = hdds[0]
            print 'Using {0} as extra HDD'.format(hdd)
            hdds.remove(hdd)
            for partition in drives[hdd]['partitions']:
                if partition in mounted:
                    fs_client.run('umount {0}'.format(partition))
            fs_client.run('parted {0} -s mklabel gpt'.format(hdd))
            fs_client.run('parted {0} -s mkpart backendfs 2MB 80%'.format(hdd))
            fs_client.run('parted {0} -s mkpart distribfs 80% 90%'.format(hdd))
            fs_client.run('parted {0} -s mkpart tempfs 90% 100%'.format(hdd))
            fs_client.run('mkfs.ext4 -q {0}1 -L backendfs'.format(hdd))
            fs_client.run('mkfs.ext4 -q {0}2 -L distribfs'.format(hdd))
            fs_client.run('mkfs.ext4 -q {0}3 -L tempfs'.format(hdd))

            extra_mountpoints = """
LABEL=backendfs /mnt/bfs         ext4    defaults,nobootwait,noatime,discard    0    2
LABEL=distribfs /mnt/dfs/local   ext4    defaults,nobootwait,noatime,discard    0    2
LABEL=tempfs    /var/tmp         ext4    defaults,nobootwait,noatime,discard    0    2
"""

            fs_client.run('mkdir -p /mnt/bfs')
            fs_client.run('mkdir -p /mnt/dfs/local')

        # Create partitions on SSD
        ssds = [drive for drive, info in drives.iteritems() if
                info['ssd'] is True and root_partition not in info['partitions']]
        ssd = None
        if len(ssds) == 0:
            if len(hdds) > 0:
                if not use_hdd_io_ssd:
                    print 'No SSD found, but one or more HDDs are found that can be used instead.'
                    print 'However, using a HDD instead of an SSD will cause severe performance loss.'
                    continue_install = Interactive.ask_yesno('Are you sure you want to continue?', default_value=False)
                    if continue_install:
                        ssd = Interactive.ask_choice(hdds, question='Choose the HDD to use as SSD replacement')
                elif use_hdd_io_ssd:
                    ssd = ssd_mountpoint
                else:
                    raise Exception('Insufficient SSD devices')
            else:
                raise Exception('No SSD found. At least one SSD (or replacing HDD) is required.')
        elif len(ssds) > 1:
            if not ssd_mountpoint:
                ssd = Interactive.ask_choice(ssds, question='Choose the SSD to use for Open vStorage')
            else:
                ssd = ssd_mountpoint
        else:
            if not ssd_mountpoint:
                ssd = ssds[0]
            else:
                ssd = ssd_mountpoint

        print 'Using {0} as SSD'.format(ssd)
        for partition in drives[ssd]['partitions']:
            if partition in mounted:
                fs_client.run('umount {0}'.format(partition))
        fs_client.run('parted {0} -s mklabel gpt'.format(ssd))
        fs_client.run('parted {0} -s mkpart cache 2MB 50%'.format(ssd))
        fs_client.run('parted {0} -s mkpart db 50% 75%'.format(ssd))
        fs_client.run('parted {0} -s mkpart mdpath 75% 100%'.format(ssd))
        fs_client.run('mkfs.ext4 -q {0}1 -L cache'.format(ssd))
        fs_client.run('mkfs.ext4 -q {0}2 -L db'.format(ssd))
        fs_client.run('mkfs.ext4 -q {0}3 -L mdpath'.format(ssd))

        fs_client.run('mkdir -p /mnt/db')
        fs_client.run('mkdir -p /mnt/cache')
        fs_client.run('mkdir -p /mnt/md')

        # Add content to fstab
        new_filesystems = """
# BEGIN Open vStorage
LABEL=db        /mnt/db    ext4    defaults,nobootwait,noatime,discard    0    2
LABEL=cache     /mnt/cache ext4    defaults,nobootwait,noatime,discard    0    2
LABEL=mdpath    /mnt/md    ext4    defaults,nobootwait,noatime,discard    0    2
{0}
# END Open vStorage
""".format(extra_mountpoints)
        must_update = False
        fstab_content = fs_client.file_read('/etc/fstab')
        if not '# BEGIN Open vStorage' in fstab_content:
            fstab_content += '\n'
            fstab_content += new_filesystems
            must_update = True
        if must_update:
            fs_client.file_write('/etc/fstab', fstab_content)

        try:
            fs_client.run('timeout -k 9 5s mountall -q || true')
        except:
            pass  # The above might fail sometimes. We don't mind and will try again
        fs_client.run('swapoff --all')
        fs_client.run('mountall -q')

    @staticmethod
    def _discover_nodes(client):
        nodes = {}
        discover_result = client.run('avahi-browse -artp 2> /dev/null | grep ovs_cluster || true')
        for entry in discover_result.split('\n'):
            entry_parts = entry.split(';')
            if entry_parts[0] == '=' and entry_parts[2] == 'IPv4':
                cluster_info = entry_parts[3].split('_')
                cluster_name = cluster_info[-2]
                node_name = cluster_info[-1]
                if cluster_name not in nodes:
                    nodes[cluster_name] = {}
                nodes[cluster_name][node_name] = entry_parts[7]
        return nodes

    @staticmethod
    def _validate_ip(ip):
        regex = '^(((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?))$'
        match = re.search(regex, ip)
        return match is not None

    @staticmethod
    def _discover_hypervisor(client):
        hypervisor = None
        module = client.run('lsmod | grep kvm || true').strip()
        if module != '':
            hypervisor = 'KVM'
        else:
            disktypes = client.run('dmesg | grep VMware || true').strip()
            if disktypes != '':
                hypervisor = 'VMWARE'
        return hypervisor

    @staticmethod
    def _remote_config_read(client, filename):
        contents = client.file_read(filename)
        with open('/tmp/temp_read.cfg', 'w') as configfile:
            configfile.write(contents)
        config = ConfigParser.ConfigParser()
        config.read('/tmp/temp_read.cfg')
        return config

    @staticmethod
    def _remote_config_write(client, filename, config):
        with open('/tmp/temp_write.cfg', 'w') as configfile:
            config.write(configfile)
        with open('/tmp/temp_write.cfg', 'r') as configfile:
            contents = configfile.read()
            client.file_write(filename, contents)

    @staticmethod
    def _replace_param_in_config(client, config_file, old_value, new_value, add=False):
        if client.file_exists(config_file):
            contents = client.file_read(config_file)
            if new_value in contents and new_value.find(old_value) > 0:
                pass
            elif old_value in contents:
                contents = contents.replace(old_value, new_value)
            elif add:
                contents += new_value + '\n'
            client.file_write(config_file, contents)

    @staticmethod
    def _exec_python(client, script):
        """
        Executes a python script on the client
        """
        return client.run('python -c """{0}"""'.format(script))

    @staticmethod
    def _change_service_state(client, name, state):
        """
        Starts/stops/restarts a service
        """

        status = SetupController._get_service_status(client, name)
        current_status = status
        if status is False and state in ['start', 'restart']:
            SetupController._start_service(client, name)
        elif status is True and state in ['stop']:
            SetupController._stop_service(client, name)
        elif status is True and state in ['restart']:
            SetupController._restart_service(client, name)
        status = SetupController._get_service_status(client, name)
        if current_status != status:
            print '  {0} status {1} to {2}'.format(name,
                                                   'running' if current_status is True else 'halted',
                                                   'running' if status is True else 'halted')

    @staticmethod
    def _update_es_configuration(es_client, value):
        # update elasticsearch configuration
        config_file = '/etc/elasticsearch/elasticsearch.yml'
        SetupController._change_service_state(es_client, 'elasticsearch', 'stop')
        SetupController._replace_param_in_config(es_client,
                                                 config_file,
                                                 '<IS_POTENTIAL_MASTER>',
                                                 value)
        SetupController._replace_param_in_config(es_client,
                                                 config_file,
                                                 '<IS_DATASTORE>',
                                                 value)
        SetupController._change_service_state(es_client, 'elasticsearch', 'start')
        SetupController._change_service_state(es_client, 'logstash', 'restart')

    @staticmethod
    def _configure_amqp_to_volumedriver(client, vpname=None):
        """
        Reads out the RabbitMQ client config, using that to (re)configure the volumedriver configuration(s)
        """
        remote_script = """
import os
import ConfigParser
from ovs.plugin.provider.configuration import Configuration
protocol = Configuration.get('ovs.core.broker.protocol')
login = Configuration.get('ovs.core.broker.login')
password = Configuration.get('ovs.core.broker.password')
vpool_name = {0}
uris = []
cfg = ConfigParser.ConfigParser()
cfg.read('/opt/OpenvStorage/config/rabbitmqclient.cfg')
nodes = [n.strip() for n in cfg.get('main', 'nodes').split(',')]
for node in nodes:
    uris.append({{'amqp_uri': '{{0}}://{{1}}:{{2}}@{{3}}'.format(protocol, login, password, cfg.get(node, 'location'))}})
from ovs.extensions.storageserver.volumestoragerouter import VolumeStorageRouterConfiguration
queue_config = {{'events_amqp_routing_key': Configuration.get('ovs.core.broker.volumerouter.queue'),
                 'events_amqp_uris': uris}}
for config_file in os.listdir('/opt/OpenvStorage/config/voldrv_vpools'):
    this_vpool_name = config_file.replace('.json', '')
    if config_file.endswith('.json') and (vpool_name is None or vpool_name == this_vpool_name):
        vsr_configuration = VolumeStorageRouterConfiguration(this_vpool_name)
        vsr_configuration.configure_event_publisher(queue_config)"""
        SetupController._exec_python(client, remote_script.format(vpname if vpname is None else "'{0}'".format(vpname)))
