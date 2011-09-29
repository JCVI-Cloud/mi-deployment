"""Fabric deployment script for creating a snapshot from an EBS volume while
automatically taking care of the system level operations.

Fabric (http://docs.fabfile.org) is used to manage the automation of a remote server.

Usage:
    fab -f volume_manipulations_fab.py -i key_file -H servername make_snapshot[:galaxy]
"""

import os, os.path, time, urllib, yaml
import datetime as dt
boto = __import__("boto")
from boto.ec2.connection import EC2Connection
from boto.s3.connection import S3Connection
from boto.s3.key import Key
from boto.exception import EC2ResponseError, S3ResponseError

from fabric.api import sudo, run, env
from fabric.contrib.console import confirm
from fabric.contrib.files import exists, settings
from fabric.colors import red, green, yellow

GALAXY_HOME = "/mnt/galaxyTools/galaxy-central"
DEFAULT_BUCKET_NAME = 'cloudman'
# -- Adjust this link if using content from another location
CDN_ROOT_URL = "http://userwww.service.emory.edu/~eafgan/content"

# EDIT FOLLOWING TWO LINES IF NEEDED/DESIRED:
# If you do not have the following two environment variables set (AWS_ACCESS_KEY_ID,
# AWS_SECRET_ACCESS_KEY), provide your credentials below and uncomment the two lines:
# os.environ['AWS_ACCESS_KEY_ID'] = "your access key"
# os.environ['AWS_SECRET_ACCESS_KEY'] = "your secret key"


# -- Fabric setup
env.user = 'ubuntu'
env.use_sudo = True
env.shell = "/bin/bash -l -c"

def make_snapshot(galaxy=None):
    """ Create a snapshot of an existing volume that is currently attached to an
    instance, taking care of the unmounting and detaching. If you specify the
    optional argument (:galaxy), the script will pull the latest Galaxy code 
    from bitbucket and perform an update before snapshotting. Else, the script 
    will prompt for the file system path to be snapshoted.
    
    In order for this to work, an instance on EC2 needs to be running with a 
    volume that wants to be snapshoted attached and mounted. The script will 
    unmount the volume, create a snaphost and offer to reattach and mount the 
    volume or create a new one from the freshly created snapshot.
    
    Except for potentially Galaxy, MAKE SURE there are no running processes 
    using the volume and that no one is logged into the instance and sitting 
    in the given directory.
    """
    time_start = dt.datetime.utcnow()
    print "Start time: %s" % time_start
    # Check if we're creating a snapshot where Galaxy is installed & running
    if galaxy=='galaxy':
        galaxy=True
        fs_path = os.path.split(GALAXY_HOME)[0]
    else:
        galaxy=False
        # Ask the user what is the path of the volume that should be snapshoted
        fs_path = raw_input("What is the path for the file system to be snapshoted? ")
    if galaxy:
        commit_num = _update_galaxy()
        _clean_galaxy_dir()
    
    instance_id = run("curl --silent http://169.254.169.254/latest/meta-data/instance-id")
    availability_zone = run("curl --silent http://169.254.169.254/latest/meta-data/placement/availability-zone")
    instance_region = availability_zone[:-1] # Truncate zone letter to get region name
    # Find the device where the file system is mounted to
    device_id = sudo("df | grep '%s' | awk '{print $1}'" % fs_path)
    # Find the EBS volume where the file system resides
    ec2_conn = _get_ec2_conn(instance_region)
    vol_list = ec2_conn.get_all_volumes()
    fs_vol = None
    for vol in vol_list:
        if vol.attach_data.instance_id==instance_id and vol.attach_data.status=='attached' and vol.attach_data.device == device_id:
            fs_vol = vol
    if fs_vol:
        print(yellow("Detected that '%s' is mounted from device '%s' and attached as volume '%s'" % (fs_path, device_id, fs_vol.id)))
        sudo("umount %s" % fs_path)
        _detach(ec2_conn, instance_id, fs_vol.id)
        if galaxy:
            desc = "Galaxy (at commit %s) and tools" % commit_num
        else:
            desc = raw_input("Provide a short snapshot description: ")
        snap_id = _create_snapshot(ec2_conn, fs_vol.id, desc)
        print(green("--------------------------"))
        print(green("New snapshot ID: %s" % snap_id))
        print(green("--------------------------"))
        if galaxy:
            if confirm("Would you like to update the file 'snaps.yaml' in '%s' bucket on S3 to include reference to the new Galaxy snapshot ID: '%s'" % (DEFAULT_BUCKET_NAME, snap_id)):
                _update_snaps_latest_file('galaxyTools', snap_id, fs_vol.size, commit_num='Galaxy at commit %s' % commit_num)
        if confirm("Would you like to make the newly created snapshot '%s' public?" % snap_id):
            ec2_conn.modify_snapshot_attribute(snap_id, attribute='createVolumePermission', operation='add', groups=['all'])
        answer = confirm("Would you like to attach the *old* volume '%s' used to make the new snapshot back to instance '%s' and mount it as '%s'?" % (fs_vol.id, instance_id, fs_path))
        if answer:
            _attach(ec2_conn, instance_id, fs_vol.id, device_id)
            sudo("mount %s %s" % (device_id, fs_path))
            if galaxy:
                _start_galaxy()
        elif confirm("Would you like to delete the *old* volume '%s' then?" % fs_vol.id):
            _delete_volume(ec2_conn, fs_vol.id)
        if not answer: # Old volume was not re-attached, maybe crete a new one 
            if confirm("Would you like to create a new volume from the *new* snapshot '%s', attach it to the instance '%s' and mount it as '%s'?" % (snap_id, instance_id, fs_path)):
                try:
                    new_vol = ec2_conn.create_volume(fs_vol.size, fs_vol.zone, snapshot=snap_id)
                    print(yellow("Created new volume of size '%s' from snapshot '%s' with ID '%s'" % (new_vol.size, snap_id, new_vol.id)))
                    _attach(ec2_conn, instance_id, new_vol.id, device_id)
                    sudo("mount %s %s" % (device_id, fs_path))
                    if galaxy:
                        answer = confirm("Would you like to start Galaxy on instance?")
                        if answer:
                            _start_galaxy()
                except EC2ResponseError, e:
                    print(red("Error creating volume: %s" % e))
        print(green("----- Done snapshoting volume '%s' for file system '%s' -----" % (fs_vol.id, fs_path)))
    else:
        print(red("ERROR: cannot run this script without boto"))
    time_end = dt.datetime.utcnow()
    print(yellow("Duration of snapshoting: %s" % str(time_end-time_start)))

## ----- Helper methods -----
def _update_galaxy():
    _stop_galaxy()
    # Because of a conflict in static/welcome.html file on cloud Galaxy and the
    # main Galaxy repository, force local change to persist in case of a merge
    print(yellow("Updating Galaxy source"))
    sudo('su galaxy -c "cd %s; hg --config ui.merge=internal:local pull --update"' % GALAXY_HOME)
    commit_num = sudo('su galaxy -c "cd %s; hg tip | grep changeset | cut -d: -f2 "' % GALAXY_HOME).strip()
    # A vanilla datatypes_conf is used so to make sure it's up to date delete it; it will be automatically recreated.
    if exists("%s/datatypes_conf.xml" % GALAXY_HOME):
        sudo('cd %s; rm datatypes_conf.xml' % GALAXY_HOME)
    print(yellow("Upgrading Galaxy database"))
    sudo('su galaxy -c "cd %s; sh manage_db.sh upgrade"' % GALAXY_HOME)
    # Start & stop Galaxy now to ensure all of the recent eggs get downloaded
    # before a snapshot is created
    print(yellow("Testing Galaxy via full start-stop"))
    _start_galaxy()
    _stop_galaxy()
    return commit_num

def _clean_galaxy_dir():
    # Clean up galaxy directory before snapshoting
    with settings(warn_only=True):
        print(yellow("Cleaning Galaxy's directory"))
        if exists("%s/paster.log" % GALAXY_HOME):
            sudo("rm %s/paster.log" % GALAXY_HOME)
        sudo("rm %s/database/pbs/*" % GALAXY_HOME)
        # set up the symlink for SAMTOOLS (remove this code once SAMTOOLS is converted to data tables)
        if exists("%s/tool-data/sam_fa_indices.loc" % GALAXY_HOME):
            sudo("rm %s/tool-data/sam_fa_indices.loc" % GALAXY_HOME)
        tmp_loc = False
        if not exists("/mnt/galaxyIndices/galaxy/tool-data/sam_fa_indices.loc"):
            sudo("touch /mnt/galaxyIndices/galaxy/tool-data/sam_fa_indices.loc")
            tmp_loc = True
        sudo("ln -s /mnt/galaxyIndices/galaxy/tool-data/sam_fa_indices.loc %s/tool-data/sam_fa_indices.loc" % GALAXY_HOME)
        if tmp_loc:
            sudo("rm /mnt/galaxyIndices/galaxy/tool-data/sam_fa_indices.loc")
        # If needed, upload the custom cloud welcome screen files
        if not exists("%s/static/images/cloud.gif" % GALAXY_HOME):
            sudo("wget --output-document=%s/static/images/cloud.gif %s/cloud.gif" % (GALAXY_HOME, CDN_ROOT_URL))
        if not exists("%s/static/images/cloud_txt.png" % GALAXY_HOME):
            sudo("wget --output-document=%s/static/images/cloud_text.png %s/cloud_text.png" % (GALAXY_HOME, CDN_ROOT_URL))
        if not exists("%s/static/welcome.html" % GALAXY_HOME):
            sudo("wget --output-document=%s/static/welcome.html %s/welcome.html" % (GALAXY_HOME, CDN_ROOT_URL))
    # Clean up configuration files form the snapshot to ensure those get
    # downloaded from cluster's (or default) bucket at cluster instantiation
    if exists("%s/universe_wsgi.ini.cloud" % GALAXY_HOME):
        sudo("rm %s/universe_wsgi.ini.cloud" % GALAXY_HOME)
    if exists("%s/tool_conf.xml.cloud" % GALAXY_HOME):
        sudo("rm %s/tool_conf.xml.cloud" % GALAXY_HOME)
    if exists("%s/tool_data_table_conf.xml.cloud" % GALAXY_HOME):
        sudo("rm %s/tool_data_table_conf.xml.cloud" % GALAXY_HOME)

def _stop_galaxy():
    if exists(os.path.join(GALAXY_HOME, "paster.pid")):
        print(yellow("Stopping Galaxy"))
        sudo('su galaxy -c "cd %s; sh run.sh --stop-daemon"' % GALAXY_HOME)
    else:
        print(yellow("Wanted to stop Galaxy but it does not seem to be running?"))

def _start_galaxy():
    print(yellow("Starting Galaxy"))
    sudo('su galaxy -c "source /etc/bash.bashrc; source /home/galaxy/.bash_profile; export SGE_ROOT=/opt/sge; cd /mnt/galaxyTools/galaxy-central; sh run.sh --daemon"')
    # Wait for Galaxy to start - obviously this may result in an infinite
    # loop so watch it...
    # Also, this assumes Galaxy runs on 127.0.0.1:8080
    counter = 0
    while True:
        galaxy_accessible = run('curl --silent 127.0.0.1:8080 > /dev/null; echo $?')
        if galaxy_accessible == '0':
            print(yellow("Galaxy started"))
            break
        print("Waiting for Galaxy to start...")
        time.sleep(6)
        counter += 1
        if counter > 20:
            print(red("This seems to be taking longer than expected. Manual check?"))

def _update_snaps_latest_file(filesystem, snap_id, vol_size, **kwargs):
    bucket_name = DEFAULT_BUCKET_NAME
    remote_file_name = 'snaps.yaml'
    remote_url = 'http://s3.amazonaws.com/%s/%s' % (bucket_name, remote_file_name)
    downloaded_local_file = "downloaded-from-%s_snaps.yaml" % bucket_name
    old_remote_file = generated_local_file = "snaps.yaml"
    urllib.urlretrieve(remote_url, downloaded_local_file)
    with open(downloaded_local_file) as f:
        snaps_dict = yaml.load(f)
    for fs in snaps_dict['static_filesystems']:
        if fs['filesystem'] == filesystem:
            fs['snap_id'] = snap_id
            fs['size'] = vol_size
    with open(generated_local_file, 'w') as f:
        yaml.dump(snaps_dict, f, default_flow_style=False)
    # Rename current old_remote_file to include date it was last modified
    date_uploaded = _get_date_file_last_modified_on_S3(bucket_name, old_remote_file)
    new_name_for_old_snaps_file = "snaps-%s.yaml" % date_uploaded
    _rename_file_in_S3(new_name_for_old_snaps_file, bucket_name, old_remote_file)
    # Save the new file to S3
    return _save_file_to_bucket(bucket_name, remote_file_name, generated_local_file, **kwargs)

def _get_bucket(bucket_name):
    s3_conn = S3Connection()
    b = None
    for i in range(0, 5):
        try:
            b = s3_conn.get_bucket(bucket_name)
            break
        except S3ResponseError: 
            print "Bucket '%s' not found, attempt %s/5" % (bucket_name, i)
            return None
    return b

def _get_date_file_last_modified_on_S3(bucket_name, file_name):
    """Return date file_name was last modified in format YYYY-MM-DD"""
    b = _get_bucket(bucket_name)
    if b is not None:
        try:
            k = b.get_key(file_name)
            lm = k.last_modified
            mlf = time.strptime(lm, "%a, %d %b %Y %H:%M:%S GMT")
            return time.strftime("%Y-%m-%d", mlf)
        except S3ResponseError, e:
            print "Failed to get file '%s' from bucket '%s': %s" % (file_name, bucket_name, e)
            return ""
        except ValueError, e:
            print "Failed to format file '%s' last modified: %s" % (file_name, e)
            return ""

def _rename_file_in_S3(new_key_name, bucket_name, old_key_name):
    b = _get_bucket(bucket_name)
    if b is not None:
        try:
            k = b.get_key(old_key_name) # copy any metadata too
            b.copy_key(new_key_name, bucket_name, old_key_name, metadata=k.metadata, preserve_acl=True)
            print "Successfully renamed file '%s' in bucket '%s' to '%s'." % (old_key_name, bucket_name, new_key_name)
            return True
        except S3ResponseError, e:
             print "Failed to rename file '%s' in bucket '%s' as file '%s': %s" % (old_key_name, bucket_name, new_key_name, e)
             return False

def _save_file_to_bucket(bucket_name, remote_filename, local_file, **kwargs):
    """ Save the local_file to bucket_name as remote_filename. Also, any additional
    arguments passed as key-value pairs, are stored as file's metadata on S3."""
    # print "Establishing handle with bucket '%s'..." % bucket_name
    b = _get_bucket(bucket_name)
    if b is not None:
        # print "Establishing handle with key object '%s'..." % remote_filename
        k = Key( b, remote_filename )
        print "Attempting to save file '%s' to bucket '%s'..." % (remote_filename, bucket_name)
        try:
            # Store some metadata (key-value pairs) about the contents of the file being uploaded
            # Note that the metadata must be set *before* writing the file
            k.set_metadata('date_uploaded', str(dt.datetime.utcnow()))
            for args_key in kwargs:
                print "Adding metadata to file '%s': %s=%s" % (remote_filename, args_key, kwargs[args_key])
                k.set_metadata(args_key, kwargs[args_key])
            print "Saving file '%s'" % local_file
            k.set_contents_from_filename(local_file)
            print "Successfully added file '%s' to bucket '%s'." % (remote_filename, bucket_name)
            answer = confirm("Would you like to make file '%s' publicly readable?" % remote_filename)
            if answer:
                k.make_public()
        except S3ResponseError, e:
            print "Failed to save file local file '%s' to bucket '%s' as file '%s': %s" % ( local_file, bucket_name, remote_filename, e )
            return False
        return True
    else:
        return False

def _attach( ec2_conn, instance_id, volume_id, device ):
    """
    Attach EBS volume to the given device (using boto).
    Try it for some time.
    """
    try:
        print "Attaching volume '%s' to instance '%s' as device '%s'" % ( volume_id, instance_id, device )
        volumestatus = ec2_conn.attach_volume( volume_id, instance_id, device )
    except EC2ResponseError, e:
        print "Attaching volume '%s' to instance '%s' as device '%s' failed. Exception: %s" % ( volume_id, instance_id, device, e )
        return False
    
    for counter in range( 30 ):
        print "Attach attempt %s, volume status: %s" % ( counter, volumestatus )
        if volumestatus == 'attached':
            print "Volume '%s' attached to instance '%s' as device '%s'" % ( volume_id, instance_id, device )
            break
        if counter == 29:
            print "Volume '%s' FAILED to attach to instance '%s' as device '%s'. Aborting." % ( volume_id, instance_id, device )
            return False
        
        volumes = ec2_conn.get_all_volumes( [volume_id] )
        volumestatus = volumes[0].attachment_state()
        time.sleep(3)
    return True

def _detach( ec2_conn, instance_id, volume_id ):
    """
    Detach EBS volume from the given instance (using boto).
    Try it for some time.
    """
    try:
        volumestatus = ec2_conn.detach_volume( volume_id, instance_id, force=True )
    except EC2ResponseError, ( e ):
        print "Detaching volume '%s' from instance '%s' failed. Exception: %s" % ( volume_id, instance_id, e )
        return False
    
    for counter in range( 30 ):
        print "Volume '%s' status '%s'" % ( volume_id, volumestatus )
        if volumestatus == 'available':
            print "Volume '%s' successfully detached from instance '%s'." % ( volume_id, instance_id )
            break
        if counter == 29:
            print "Volume '%s' FAILED to detach to instance '%s'." % ( volume_id, instance_id )
        time.sleep(3)
        volumes = ec2_conn.get_all_volumes( [volume_id] )
        volumestatus = volumes[0].status

def _create_snapshot(ec2_conn, volume_id, description=None):
    """
    Create a snapshot of the EBS volume with the provided volume_id. 
    Wait until the snapshot process is complete (note that this may take quite a while)
    """
    s_time = dt.datetime.now()
    print(yellow("Initiating snapshot of EBS volume '%s' in region '%s' (start time %s)" % (volume_id, ec2_conn.region.name, s_time)))
    snapshot = ec2_conn.create_snapshot(volume_id, description=description)
    if snapshot: 
        while snapshot.status != 'completed':
            print "Snapshot '%s' progress: '%s'; status: '%s'; duration: %s" % (snapshot.id, snapshot.progress, snapshot.status, str(dt.datetime.now()-s_time).split('.')[0])
            time.sleep(6)
            snapshot.update()
        print "Creation of snapshot for volume '%s' completed: '%s'" % (volume_id, snapshot)
        return snapshot.id
    else:
        print "Could not create snapshot from volume with ID '%s'" % volume_id
        return False

def _delete_volume(ec2_conn, vol_id):
    try:
        ec2_conn.delete_volume(vol_id)
        print "Deleted volume '%s'" % vol_id
    except EC2ResponseError, e:
        print "ERROR deleting volume '%s': %s" % (vol_id, e)

def _get_ec2_conn(instance_region='us-east-1'):
    regions = boto.ec2.regions()
    print "Found regions: %s; trying to match to instance region: %s" % (regions, instance_region)
    region = None
    for r in regions:
        if instance_region in r.name:
            region = r
            break
    if not region:
        print "ERROR discovering a region; try running this script again using 'rebundle' as the last argument."
        return None
    try:
        ec2_conn = EC2Connection(region=region)
        return ec2_conn
    except EC2ResponseError, e:
        print "ERROR getting EC2 connections: %s" % e
        return None
