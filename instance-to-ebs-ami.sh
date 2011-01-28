#!/bin/bash
# Run this script on the instance to be bundled

EBS_DEVICE='/dev/sdh' # Future AMI file system volume
EBS_MOUNT_POINT='/mnt/ebs'
IMAGE_DEVICE='/dev/sdj' # Temporary vol used to copy the root file system onto during rebundling
IMAGE_MOUNT_POINT='/mnt/tmp_ebs'

mkfs.xfs -f $IMAGE_DEVICE
if [ -d $IMAGE_MOUNT_POINT ]; then
    rm -rf $IMAGE_MOUNT_POINT
fi
mkdir -m 000 -p $IMAGE_MOUNT_POINT
mount -t xfs $IMAGE_DEVICE $IMAGE_MOUNT_POINT

# Newer Ubuntu images use a 'LABEL=uec-rootfs' for the root file system device 
# so check if it's present in /etc/fstab and create the future root file system 
# using that label
if grep -q "uec-rootfs" /etc/fstab; then
    mkfs.xfs -f -L uec-rootfs $EBS_DEVICE
else 
    mkfs.xfs -f $EBS_DEVICE
fi
mkdir -m 000 -p $EBS_MOUNT_POINT
mount -t xfs $EBS_DEVICE $EBS_MOUNT_POINT

#make a local working copy
rsync --stats -ax --exclude /root/.bash_history --exclude /home/*/.bash_history --exclude /etc/ssh/ssh_host_* --exclude /etc/ssh/moduli --exclude /etc/udev/rules.d/*persistent-net.rules --exclude /var/lib/ec2/* --exclude=/mnt/* --exclude=/proc/* --exclude=/tmp/* --exclude /root/.ssh/* --exclude /home/ubuntu/.ssh/* --exclude /var/lib/rabbitmq/mnesia / $IMAGE_MOUNT_POINT

# Because we're using xfs as the root file system, edit /etc/fstab on the image to reflect so
sed 's/ext3/xfs /' $IMAGE_MOUNT_POINT/etc/fstab > $IMAGE_MOUNT_POINT/etc/fstab2
mv $IMAGE_MOUNT_POINT/etc/fstab2 $IMAGE_MOUNT_POINT/etc/fstab

#clear out log files
cd $IMAGE_MOUNT_POINT/var/log
for i in `ls ./**/*`; do
  echo $i && echo -n> $i
done
# Clean contents of authorized_keys where public key of user starting an instance is stored
# > $IMAGE_MOUNT_POINT/root/.ssh/authorized_keys
# > $IMAGE_MOUNT_POINT/home/ubuntu/.ssh/authorized_keys

cd $IMAGE_MOUNT_POINT
tar -cpSf - -C ./ . | tar xf - -C $EBS_MOUNT_POINT
#NOTE, You could rsync / directly to EBS_MOUNT_POINT, but this tar trickery saves some space in the snapshot

umount $EBS_MOUNT_POINT
umount -lf $IMAGE_MOUNT_POINT
