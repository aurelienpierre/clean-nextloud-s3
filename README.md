## Intro

Nextcloud is a great self-hosted cloud software, but I'm underwhelmed by its garbage collection when using Amazon S3 buckets : 

- it doesn't necessarily remove thumbnails associated with deleted files. The [PR adding this feature to `occ`](https://github.com/nextcloud/server/pull/35189) has been stale since 2022, and the [core bug](https://github.com/nextcloud/server/issues/20344) has been fixed only in March 2023 for NC 25.0.2 (older previews may still be there),
- it doesn't necessarily remove S3 objects associated with deleted files ([issue filed in 2020](https://github.com/nextcloud/server/issues/20333), still not fixed),
- each file preview thumbnail is a new row in Nextcloud DB `oc_filecache` table, wether or not the thumbnail has actually been generated and exists on the S3 storage. For example, I had more than 2.5 millions empty preview entries in my DB (1.8 GB of dead DB weight), for an S3 bucket that had around 125k files,
- The OCC commands `occ files:cleanup` or `occ files:scan-app-data` do nothing when using S3 as primary storage, they work only in the typical "everything on server disk" setup.

You may well end up paying AWS hosting for files that technically don't exist anymore on your Nextcloud instance, because the S3 objets are still there, because Nextcloud garbage collection is failing. Indeed, Nextcloud seems to be very much designed around the (old-school) idea that you will have everything hosted on the same server, on a good old hard drive that will hold everything (OS, apps, web server and files). And the Nextcloud dev team doesn't seem to care _(instead, they developed cute AI integrations to put your server on its knees and a chat plugin supporting videoconferencing through WebRTC competing with Matrix/Jitsi 5 years too late in the game…)_.

"All-in-one server volume" is a suboptimal design because server SSD storage space is 3 to 5 times more expensive than AWS S3 space. Separating server backend (LAMP + Nextcloud) from file hosting (S3 bucket) allows to have a 15-20 GB volume for your whole server, and S3 buckets elsewhere. That gives you the option to replicate your S3 bucket somewhere else in the world (for backup or redundant local access), and to backup your whole server with full-volume snapshots (0.05 $/GB/month), which can be restored in a couple of minutes from AWS console. All in all, it's easier to admin and more cost-efficient.

But Nextcloud support for this decentralized architecture is very basic. _For context, see my topic on NC forum: [Suspiciously bloated MariaDB](https://help.nextcloud.com/t/suspiciously-bloated-mariadb-database/154926/16)._

## What this script does

- Remove database entries and S3 objects for orphaned file thumbnails (original file doesn't exist anymore),
- Remove orphaned S3 objects (no corresponding database entry),
- Remove orphaned DB entries (no corresponding S3 object),
- Remove DB entries for empty folders (mostly previews generation that went south),
- Backup database recordings and S3 objects locally before deleting anything,

The script can run from within your Nextcloud server or locally on your computer (better to backup things). You are advised to run the script from outside your Nextcloud server as it can be quite heavy on your computational and storage resources.

## Warnings

This works only for Nextcloud instances using MySQL/MariaDB database __and__ using AWS S3 buckets as the __primary storage__.

Everything relies here on the assumption that the given S3 bucket is used __only__ by a single Nextcloud instance, meaning any file found there should have a `fileid` match in Nextcloud database table `oc_filecache`, and any file not having a match is an orphan. __If other services are using the same S3 bucket (even in their own folder), this script will remove their files.__

We will backup S3 and database objects locally, on the disk from where the script is run. Make sure you have enough storage space available for the task at hand.

## Prerequisites

### Dependencies

Install Python packages :

```bash
python -m pip install -r requirements.txt
```

You will need a MySQL/MariaDB client on the computer running the script.

### Create config file

Copy `config.toml.example` to `config.toml` and modify the parameters. The config names match the ones in `nextcloud/config/config.php`.

### Put Nextcloud in maintenance mode

Just to avoid sync misfires with Nextcloud clients during the process, on your Nextcloud server run:

```bash
sudo -u www-data php /var/www/nextcloud/occ maintenance:mode --on
```

### Clean-up file versions and trashbins

This script doesn't deal with Nextcloud database tables `oc_files_trashbin` and `oc_versions`. If an entry is removed from `oc_filecache` by this script, it could still be referenced in either (or both) `oc_files_trashbin` or `oc_versions`, leading to an orphan reference that may cause issues in the future (not tested).

To avoid inconsistencies, you should purge versions and trashbins before running this script (warn your users about it) :

```bash
sudo -u www-data php /var/www/nextcloud/occ trashbin:cleanup --all-users
sudo -u www-data php /var/www/nextcloud/occ versions:expire
sudo -u www-data php /var/www/nextcloud/occ versions:cleanup
```

This is not mandatory and you can still run it after the script if `FileException: could not find urn:oid:...` appear in your logs later.


## Prepare remote accesses

If you run this script from within your Nextcloud server, you can disregard the following.

### S3 bucket

If you followed the proper security measures [here](https://aws.amazon.com/fr/blogs/opensource/scale-your-nextcloud-with-storage-on-amazon-simple-storage-service-amazon-s3/), you should have restricted bucket access to the Nextcloud server IP through AWS IAM permissions. If you are not running this script from your Nextcloud server, you should temporarily allow your local IP.

### MySQL

Same stuff, if running this script from outside the Nextcloud server, ensure your local IP is allowed on your MySQL server and the port `3306` (or anything MySQL uses) is open to you.

## Usage

### Backup your MySQL database

From within your server (or through SSH), use : 

```bash
mysqldump -u USER -p DATABASE_NAME > nextcloud.sql
```

(replace `USER` and `DATABASE_NAME` accordingly).

Note : you can't dump on your local computer from a distant server, you need to dump from server to server, then (if needed) download the dump through (s)FTP or SSH.


### Run the diagnostic and cleanup script

```bash
python main.py
```

The diagnostic part runs first and displays its report. You will then be prompted to optionally proceed to deleting orphans.

## Post-use

Don't forget to reset your S3 bucket permissions to the bare minimum if you changed them, close MySQL remote access, and disable Nextcloud maintenance mode.

## Restoring backups

Nextcloud database and S3 buckets are tied by `fileid`, which is the primary integer key (index) of the database table `oc_filecache` : on your S3 bucket, you will find the matching files named `urn:oid:fileid` where you just have to replace `fileid` by its integer value from database.

On your server, use `sudo -u www-data tail /path/to/nextcloud/data/nextcloud.log` to check the log for missing or unfound `urn:oid:` objects. If any, you will have to restore them manually.

### S3 objects

This scripts backs up S3 objects locally on disk using the same `urn:oid:fileid` naming convention, in a local `s3-backup` folder. Amazon S3 has a web UI that lets you download files manually, you can directly drop the requested objects backups back in the bucket with no further operation.

### Full DB

If you need to restore your full database backup later, that's how you do it :

```bash
mysql -u USER -p DATABASE_NAME < nextcloud.sql # don't blindly copy-paste if you don't need to
```

### DB entries (rows)

Before deletion from DB, each database entry from `oc_filecache` is backed up in the local `db-backup` folder, as a JSON file, named after the `fileid` of the original row. In that file, you will find the list of "columns" fields in the same order it was in database.

You can either script your own method to put back those rows in database, or use PHPMyAdmin to manually insert fields for the appropriate rows.

__WARNING: the (truly) empty folders are deleted from DB without backup. That should be safe since they are empty. It's another story if Nextcloud misidentified them as empty.__ In that case, you will need to use the full DB backup recovery.
