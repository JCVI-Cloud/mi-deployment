"""
Used to setup the environment needed for rsync-ing data to an EBS volume and
creating a snapshot from the created volume. This process entails starting an 
instance, creating the appropriate volume/file system, and rsync-ing the data.
After the copying is complete, this script can be invoked again to clean up the
environment, namely to detach the volume, terminate the instance and create a
new EBS snapshot.
This script performs the necessary steps to setup the environment for copying 
the data but it does not actually copy the data. The reason behind this is that
the data copying is likely to take a long so it is better done as a standalone
process. As a result, the script should be invoked twice: the first time 
to setup the environment, then manually copying the data to the instance (this
script provides you the exact command to run) and then invoking this script 
again to cleanup the environment.

Requires fabric, boto

Usage: 
    fab -f local_to_ebs_fab.py <setup | cleanup>
"""

import boto, time, os, datetime, socket, yaml, sys
from boto.regioninfo import RegionInfo
from boto.ec2.connection import EC2Connection
from boto.exception import EC2ResponseError, BotoServerError
from fabric.api import sudo, env, local
from fabric.contrib.console import confirm
from fabric.contrib.files import exists, settings, hide
from fabric.context_managers import settings as v_settings # virtualized settings: see http://stackoverflow.com/questions/2326797/how-to-set-target-hosts-in-fabric-file

# Use EBS-backed Ubuntu for the specified region that corresponds to INSTANCE_TYPE 
# below (http://uec-images.ubuntu.com/releases/10.04/release/). Also, for 
# eucalyptus cloud: emi-<###>; for EC2: ami-<###>
# Sample AMI/EMI: "ami-b8f405d1", "emi-E02E107B"
AMI = ""
VOLUME_SIZE = 0             # If creating a new volume (i.e., not recreating one from a snapshot), you must specify volume size, else set to None
SNAP_ID = None              # If recreating a volume from a snapshot, specify snap ID here, else set to None
INSTANCE_ID = None          # If wanting to use an existing instance, specify instance ID here
DEST_DATA_DIR = None        # Path where volume should get mounted to on the instance and data copied to, e.g., /mnt/galaxyIndices
SRC_DATA_DIR = None         # Local path where data should be copied from, e.g., /local/galaxyIndices
SNAP_DESCRIPTION = ""       # Snapshot description to be saved once the snapshot is created
DEVICE = '/dev/sdb'         # Which device to attach the volume to (e.g., /dev/sdb for Eucalyptus; /dev/sdg for EC2)
CLOUD = "eucalyptus"        # 'eucalyptus' or 'ec2'
REGION_NAME = 'emorycloud'  # 'emorycloud' or (for AWS): eu-west-1, us-east-1, us-west-1, ap-southeast-1
os.environ['AWS_ACCESS_KEY_ID'] = "your access key"
os.environ['AWS_SECRET_ACCESS_KEY'] = "your secret key"
INSTANCE_TYPE = 'm1.large'  # t1.micro (64bit), m1.small, m1.large (64bit)


# No need to modify this unless you have a security group with the same name
# NOTE: If this group already exist at time of invocation - it will be deleted!
SG_NAME = "tmp_snap_copy_sg"
# No need to modify this unless you have a key pair with the same name
# NOTE: If this key pair already exist at time of invocation - it will be deleted!
KP_NAME = "tmp_snap_copy_kp"
KP_FILE = "/tmp/%s" % KP_NAME
# Configuration file for this script where info is saved between the environemnt setup and tear down
C_FILE = '/tmp/copy_config.yaml'

## ---------------------------------- Driver -----------------------------------
def setup():
    start_time = datetime.datetime.now()
    print "Start time: %s" % start_time
    _load_env() # Load necessary env
    _check_config() # Check consistency of configuration
    _create_env() # Start instance & attach data vol
    _setup_env() # Configure instance
    _rsync()
    _create_config_file()
    end_time = datetime.datetime.now()
    print "End time: %s; setup duration: %s" % (end_time, (end_time-start_time))

def cleanup():
    start_time = datetime.datetime.now()
    print "Start time: %s" % start_time
    _load_env()
    _load_config_file()
    _get_instance_ref()
    _check_config()
    _do_cleanup()
    end_time = datetime.datetime.now()
    print "End time: %s; cleanup duration: %s" % (end_time, (end_time-start_time))

## ------------------------------ Action methods -------------------------------
def _load_env():
    """Setup for a Ubuntu 10.04 """
    env.user = 'ubuntu'
    env.shell = "/bin/bash -l -c"
    env.use_sudo = True
    env.disable_known_hosts = True
    env.vol_device = DEVICE
    env.instance = INSTANCE_ID # Save handle to created EC2 instance object or INSTANCE_ID (which will lead to instance object)
    env.volume = None # Save handle to created EC2 volume object

def _create_env():
    inst = _start_instance()
    if inst:
        _setup_volume(inst)

def _setup_env():
    _install_packages()
    _mount_ebs()

def _rsync():
    cmd = 'time rsync -avz --delete -e "ssh -o StrictHostKeyChecking=no -i %s" %s/* %s@%s:%s/.' % (KP_FILE, SRC_DATA_DIR, env.user, env.instance.public_dns_name, DEST_DATA_DIR)
    print "\n\n----------------------------------------------------------"
    print "Connect to the remote instance using the following command:"
    print "  ssh -o StrictHostKeyChecking=no -i %s %s@%s" % (KP_FILE, env.user, env.instance.public_dns_name)
    print "----------------------------------------------------------"
    print "rsync command (run it like so from local machine):"
    print "  %s" % cmd
    print "----------------------------------------------------------"
    answer = confirm("Would you like this script to run rsync (answer 'No' if you want to run rsync by hand)?", default=False)
    if answer:
        print "Now executing rsync command: %s" % cmd
        local(cmd)

def _do_cleanup():
    with v_settings(host_string=env.hosts[0]):
        ec2_conn = _get_conn()
        with settings(warn_only=True):
            sudo("umount %s" % DEST_DATA_DIR)
        if _get_volume_ref(env.instance, ec2_conn):
            if _detach_volume(ec2_conn, env.volume.id, env.instance.id):
                _terminate_instance(env.instance, ec2_conn)
                if _create_snap(ec2_conn, env.volume.id):
                    _delete_volume(ec2_conn, env.volume.id)
                    os.remove(C_FILE) # Delete configuration file
        else:
            sudo("mount %s %s" % (env.vol_device, DEST_DATA_DIR))

## ------------------------------ Utility methods ------------------------------
def _check_config():
    """ Make sure the configuration has been setup correctly before taking action """
    if os.environ['AWS_ACCESS_KEY_ID'] is None or os.environ['AWS_SECRET_ACCESS_KEY'] is None:
        print "ERROR: you must provide credentials"
        sys.exit(1)
    if AMI is None:
        print "ERROR: you must specify an AMI or EMI"
        sys.exit(1)
    regions = []
    if CLOUD=='eucalyptus':
        regions = ['emorycloud']
        if str(AMI).split('-')[0] != 'emi':
            print "ERROR: specified AMI '%s' does not seem correct; it should be in the following format 'emi-<###>'" % AMI
            sys.exit(1)
    elif CLOUD=='ec2':
        regions = ['eu-west-1', 'us-east-1', 'us-west-1', 'ap-southeast-1']
        if str(AMI).split('-')[0] != 'ami':
            print "ERROR: specified AMI '%s' does not seem correct; it should be in the following format 'ami-<###>'" % AMI
            sys.exit(1)
    if not REGION_NAME in regions:
        print "ERROR: specified region '%s' not valid for cloud '%s'" % (REGION_NAME, CLOUD)
        sys.exit(1)
    if (INSTANCE_ID or env.instance) and not env.key_filename:
        print "ERROR: when using an existing instance (%s), you must specify -i <key> option. You should be able to find it in '%s'" % (env.instance, C_FILE)
        sys.exit(1)
    if not os.path.exists(SRC_DATA_DIR):
        print "ERROR: source directory SRC_DATA_DIR='%s' does not exist." % SRC_DATA_DIR
        sys.exit(1)
    print "Configuration check OK"
    return True

def _start_instance():
    inst = None
    ec2_conn = _get_conn()
    # If an instance has been specified, try using it
    if env.instance:
        return _get_instance_ref(ec2_conn)
    # Create a complete environment for connecting to an instance and start one
    try:
        # Temporarily create a security group in the region that opens port 22
        if not _create_security_group(ec2_conn):
            print "Problem creating the temporary security group"
            return None
        # Temporarily create a a key pair in each of the regions that will allow access to the instances
        if not _create_key_pair(ec2_conn, KP_FILE):
            print "Problem creating the temporary key pair"
            return None
        # Start an instance in specified region
        print "Starting an instance in region '%s'" % ec2_conn.region.name
        reservation = ec2_conn.run_instances(image_id=AMI,
                                          key_name=KP_NAME,
                                          security_groups=[SG_NAME],
                                          instance_type=INSTANCE_TYPE)
        if reservation:
            inst = reservation.instances[0]
            print " - new instance ID: %s" % inst.id
        else:
            print "ERROR, did not get reservation object back when starting an instance?"
    except BotoServerError, e:
        print "Server ERROR starting instance: %s" % e
        return None
    except EC2ResponseError, e:
        print "ERROR starting instance: %s" % e
        return None
    # Wait until instances are 'running'
    for counter in range(40):
        print "Waiting %s sec on instance '%s' to get running, current status: %s (%s/40) " % (counter*6, inst.id, inst.state, counter)
        if inst.state == 'running':
            env.hosts = [inst.public_dns_name]
            env.instance = inst
            for i in range(10):
                if _test_ssh(inst.public_dns_name):
                    print "Instance '%s' SSH OK" % inst.id
                    return inst
                time.sleep(6)
        if counter == 39:
            print "ERROR: instance '%s' FAILED to get to state 'running' or SSH not functional?" % inst.id
            return None
        time.sleep(6)
        inst.update()

def _setup_volume(inst):
    vol, snap = None, None
    vol_size = VOLUME_SIZE
    ec2_conn = _get_conn()
    if SNAP_ID:
        snap = ec2_conn.get_all_snapshots([SNAP_ID])[0]
        vol_size = snap.volume_size
    if not _get_volume_ref(inst, ec2_conn, snap):
        if _create_vol(ec2_conn, inst.placement, vol_size, SNAP_ID):
            return _attach_vol(ec2_conn, env.volume.id, inst.id)
    return False

def _test_ssh(ip_addr):
    """ Test SSH connectivity to the instance; if this is not done, fabric connection fails pretty much every time"""
    print "Testing SSH connectivity to instance with IP address '%s'" % ip_addr
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        port = 22
        sock.settimeout(1)
        sock.connect((ip_addr, port))
        return True
    except socket.timeout:
        return False
    except socket.error, e:
        if e.errno != 111:
            return False
    finally:
        sock.close()
    return True

def _get_conn():
    if CLOUD=='eucalyptus':
        return _get_ec_conn()
    else:
        return _get_ec2_conn()

def _get_ec2_conn():
    # Get regions
    regions = boto.ec2.regions()
    # print "Found regions: %s; trying to match to instance region: %s, %s" % (regions, REGION_NAME)
    region = None
    for r in regions:
        if REGION_NAME in r.name:
            region = r
            break
    if not region:
        print "ERROR discovering regions."
        return None
    try:
        ec2_conn = EC2Connection(region=region)
        return ec2_conn
    except EC2ResponseError, e:
        print "ERROR getting EC2 connections: %s" % e
        return None

def _get_ec_conn():
    try:
        region = RegionInfo(name="emorycloud", endpoint="170.140.144.33")
        ec2_conn = boto.connect_ec2(aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'] ,
                                    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
                                    is_secure=False,
                                    region=region,
                                    port=8773,
                                    path="/services/Eucalyptus")
        return ec2_conn
    except EC2ResponseError, e:
        print "ERROR getting EC2 connections: %s" % e
        return None

def _create_security_group(ec2_conn):
    try:
        sg = ec2_conn.get_all_security_groups([SG_NAME])[0]
        if sg:
            ec2_conn.delete_security_group(SG_NAME)
            print "Deleted previously existing security group '%s' in region %s" % (SG_NAME, ec2_conn.region.name)
    except Exception:
        print "Exception checking SG '%s' in region '%s' (probably SG just does not exist; continuing)" \
            % (SG_NAME, ec2_conn.region.name)
    try:
        sg = ec2_conn.create_security_group(SG_NAME, "Temporary SG, automatically created for the duration of copying data (%s)" % datetime.datetime.utcnow())
        print "Created temporary security group '%s' in region '%s'\n" % (SG_NAME, ec2_conn.region.name)
        return sg.authorize('tcp', 22, 22, '0.0.0.0/0')
    except EC2ResponseError, e:
        print "ERROR creating security group '%s' in region %s: %s" % (SG_NAME, ec2_conn.region.name, e)
        return False

def _create_key_pair(ec2_conn, file_name):
    try:
        kp = ec2_conn.get_all_key_pairs([KP_NAME])[0]
        if kp:
            ec2_conn.delete_key_pair(KP_NAME)
            print "Deleted previously existing key pair '%s' in zone %s" % (KP_NAME, ec2_conn.region.name)
    except Exception:
        print "Exception checking KP '%s' in region '%s' (probably KP just does not exist; continuing)" \
            % (KP_NAME, ec2_conn.region.name)
    try:
        kp = ec2_conn.create_key_pair(KP_NAME)
        with open(file_name, 'w') as f:
            f.write(kp.material)
        os.system('chmod 600 %s' % file_name)
        env.key_filename = file_name
        print "Created temporary key pair '%s' in region '%s'; saved it as file '%s'\n" % (KP_NAME, ec2_conn.region.name, file_name)
    except EC2ResponseError, e:
        print "ERROR creating key pair '%s' in region %s: %s" % (KP_NAME, ec2_conn.region.name, e)
        return False
    return True

def _create_vol(ec2_conn, zone, size, snap_id=None):
    try:
        if not size and not snap_id:
            size = raw_input("Now snapshot ID, nor new volume size were specified. Specify size now (just the number in GB - e.g., 5): ")
        vol = ec2_conn.create_volume(size, zone, snapshot=snap_id)
        print "Created a volume '%s' in region '%s' of size %sGB" % (vol.id, ec2_conn.region.name, vol.size)
        env.volume = vol
        return True
    except EC2ResponseError, e:
        print "ERROR creating volume: %s" % e
        return False

def _attach_vol(ec2_conn, vol_id, inst_id):
    """
    Attach EBS volume to the given device. Try it for some time.
    """
    #FIXME: Attaching EmoryCloud EBS goes to 'attaching' state but then to 'None'?
    try:
        volumestatus = ec2_conn.attach_volume(vol_id, inst_id, env.vol_device)
    except EC2ResponseError, e:
        print "ERROR attaching volume '%s' to instance '%s' as device '%s': %s" % (vol_id, inst_id, env.vol_device, e)
        return False
    for counter in range(40):
        print "Attach attempt %s/40, volume status: %s" % (counter, volumestatus)
        if volumestatus == 'attached':
            print "Volume '%s' attached to instance '%s' in region '%s' as device '%s'" % (vol_id, inst_id, ec2_conn.region.name, env.vol_device)
            break
        elif volumestatus is None:
            print "Trying to attach vol '%s' again because first time did not work (Eucalyptus issue)?" % vol_id
            try:
                volumestatus = ec2_conn.attach_volume(vol_id, inst_id, env.vol_device)
            except EC2ResponseError, e:
                print "ERROR (but continuing) re-attaching volume '%s' to instance '%s' as device '%s': %s" % (vol_id, inst_id, env.vol_device, e)
        if counter == 39:
            print "Volume '%s' FAILED to attach to instance '%s' in region %s as device '%s'. Aborting." % (vol_id, inst_id, ec2_conn.region.name, env.vol_device)
            return False
        volumes = ec2_conn.get_all_volumes([vol_id])
        volumestatus = volumes[0].attachment_state()
        time.sleep(3)
    return True

def _get_instance_ref(ec2_conn=None):
    """ If instance ID has been specified in the env, try to reconnect to the instance """
    if not ec2_conn:
        ec2_conn = _get_conn()
    try:
        if env.instance:
            rs = ec2_conn.get_all_instances([env.instance])
            inst = rs[0].instances[0]
            env.hosts = [inst.public_dns_name]
            env.instance = inst
            print "Connected to instance '%s' with IP: %s" % (env.instance, inst.public_dns_name)
            # print "env.key_filename: %s; env.hosts: %s" % (env.key_filename, env.hosts)
            return inst
    except Exception, e:
        print "ERROR reconnecting to specified instance ID '%s': %s" % (env.instance, e)
        return None

def _get_volume_ref(inst, ec2_conn=None, snap=None):
    """ Try to discover any volumes already attached to given instance """
    if not ec2_conn:
        ec2_conn = _get_conn()
    try:
        print "Trying to discover any already attached volumes to instance '%s'..." % inst.id
        vols = ec2_conn.get_all_volumes()
        for v in vols:
            v_dev = None
            if v.attach_data.device:
                try:
                    # Eucalyptus returns v.attach_data.device value as 'unknown,requested:/dev/sdb' so fix it up a bit...
                    v_dev = str(v.attach_data.device).split(':')[1]
                except:
                    v_dev = v.attach_data.device
            if v.attach_data.status=='attached' and v.attach_data.instance_id==inst.id and v_dev==env.vol_device:
                print "Found volume '%s' attached as '%s' to instance '%s'" % (v, v_dev, inst.id)
                env.volume = v
                return True
    except EC2ResponseError, e:
        print "Error checking for attached volumes: %s" % e
    print "No already attached volumes detected."
    return False

def _install_packages():
    """Update the system and install needed packages using apt-get"""
    with v_settings(host_string=env.hosts[0]):
        # sudo('apt-get -y update')
        # run('export DEBIAN_FRONTEND=noninteractive; sudo -E apt-get upgrade -y') # Ensure a completely noninteractive upgrade
        # sudo('apt-get -y dist-upgrade')
        packages = ['xfsprogs']
        for package in packages:
            sudo("apt-get -y --force-yes install %s" % package)

def _mount_ebs():
    with v_settings(host_string=env.hosts[0]):
        if not exists(DEST_DATA_DIR):
            sudo("mkdir -p %s" % DEST_DATA_DIR)
        # Check if DEST_DATA_DIR is empty before attempting to mount
        with settings(hide('stderr'), warn_only=True): 
            result = sudo('[ "$(ls -A %s)" ]' % DEST_DATA_DIR)
        if result.failed:
            print "Directory '%s' is empty. Good." % DEST_DATA_DIR
            if not SNAP_ID:
                # If not recreating a volume from a snapshot, create file system before mounting
                sudo("mkfs.xfs %s" % env.vol_device)
            sudo("mount -t xfs %s %s" % (env.vol_device, DEST_DATA_DIR))
            sudo("chown %s %s" % (env.user, DEST_DATA_DIR))
        else:
            print "ERROR: data dir '%s' is not empty? Did not mount device '%s'" % (DEST_DATA_DIR, env.vol_device)

def _detach_volume(ec2_conn, vol_id, inst_id):
    """
    Detach EBS volume from the given instance. Try it for some time.
    """
    try:
        volumestatus = ec2_conn.detach_volume(vol_id, inst_id, force=True)
    except EC2ResponseError, e:
        print "Detaching volume '%s' from instance '%s' failed. Exception: %s" % (vol_id, inst_id, e)
        return False
    for counter in range(30):
        print "Volume '%s' status '%s'" % (vol_id, volumestatus)
        if volumestatus == 'available':
            print "Volume '%s' successfully detached from instance '%s' in region %s." % (vol_id, inst_id, ec2_conn.region.name)
            break
        if counter == 29:
            print "Volume '%s' FAILED to detach to instance '%s' in region %s." % (vol_id, inst_id, ec2_conn.region.name)
            return False
        time.sleep(4)
        volumes = ec2_conn.get_all_volumes([vol_id])
        volumestatus = volumes[0].status
    return True

def _create_snap(ec2_conn, vol_id, snap_description=SNAP_DESCRIPTION):
    print "Initiating creation of a snapshot for the volume '%s'" % vol_id
    snapshot = ec2_conn.create_snapshot(vol_id, description=snap_description)
    if snapshot: 
        counter = 0
        while snapshot.status != 'completed':
            print "Snapshot '%s' progress (%s sec): '%s'; status: '%s'" % (snapshot.id, 6*counter, snapshot.progress, snapshot.status)
            time.sleep(6)
            snapshot.update()
            counter += 1
        print "Creation of a snapshot for the volume '%s' completed: '%s'" % (vol_id, snapshot.id)
        return True
    else:
        print "ERROR: could not create snapshot from volume '%s'" % vol_id
    return False

def _delete_volume(ec2_conn, vol_id):
    try:
        ec2_conn.delete_volume(vol_id)
        print "Deleted volume '%s'" % vol_id
    except EC2ResponseError, e:
        print "ERROR deleting volume '%s': %s" % (vol_id, e)

def _terminate_instance(inst, ec2_conn=None):
    if not ec2_conn:
        ec2_conn = _get_conn()
    try:
        ec2_conn.terminate_instances([inst.id])
        inst.update()
        print "Initiated termination of instance '%s' (status: %s)" % (inst.id, inst.state)
        # Clean up the temporary security group
        ec2_conn.delete_security_group(SG_NAME)
        # Clean up the temporary key pair
        _delete_key_pair(ec2_conn, KP_FILE)
    except EC2ResponseError, e:
        print "ERROR terminating instances: %s" % e

def _delete_key_pair(ec2_conn, file_name):
    try:
        ec2_conn.delete_key_pair(KP_NAME)
        os.remove(file_name)
        return True
    except EC2ResponseError, e:
        print "ERROR deleting key pair: %s" % e
        return False
    except OSError, e:
        print "ERROR deleting key pair file: %s" % e
        return False

def _create_config_file():
    config = {}
    config['config'] = [{'instance': env.instance.id}, 
                        {'instance_public_dns': env.instance.public_dns_name}, 
                        {'placement': env.instance.placement}, 
                        {'volume': env.volume.id}, 
                        {'key_file': KP_FILE}]
    with open(C_FILE, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    # print "----------------------------------------------------------"
    # print "Access created instance (%s) using the following command:" % env.instance.id
    # print "  ssh -o StrictHostKeyChecking=no -i %s %s@%s" % (KP_FILE, env.user, env.instance.public_dns_name)
    # print "----------------------------------------------------------"

def _load_config_file():
    with open(C_FILE) as f:
        conf = yaml.load(f)
    # inst_id = vol_id = None
    # Extract references for source instance and volume
    for i in conf['config']:
        if i.has_key('instance') and not env.instance:
            env.instance = i['instance']
        elif i.has_key('volume'):
            env.volume = i['volume']
        elif i.has_key('key_file'):
            env.key_filename = i['key_file']
