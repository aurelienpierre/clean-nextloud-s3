"""
Clean-up orphaned Amazon S3 objects from Nextcloud database:

  - deleted files,
  - previews of deleted files,

"""

import os
import toml
import mysql.connector
import boto3
import json

config = toml.load("config.toml")

# Open server sessions
session = boto3.Session(aws_access_key_id = config["s3"]["key"],
                        aws_secret_access_key = config["s3"]["secret"],
                        region_name = config["s3"]["region"])
s3 = session.resource('s3')
mybucket = s3.Bucket(config["s3"]["bucket"])

mydb = mysql.connector.connect(
  host = config["mysql"]["dbhost"],
  user = config["mysql"]["dbuser"],
  password = config["mysql"]["dbpassword"],
  database = config["mysql"]["dbname"]
)

def backup_and_delete_s3(id, bucket):
  filename = f"urn:oid:{id}"

  # Backup the s3 object locally... you never know
  with open(os.path.join('s3-backup', filename), 'wb') as f:
      bucket.download_fileobj(filename, f)

  # Delete the distant S3 object
  bucket.delete_objects(Delete={
        'Objects': [
            {
                'Key': filename,
            },
        ],
  })


def backup_and_delete_db(id, db):
  db.reconnect()
  cursor = db.cursor()
  cursor.execute(f"select * from oc_filecache where fileid = {id}")
  result = cursor.fetchall()

  # Backup database entry locally
  with open(os.path.join('db-backup', str(id) + ".json"), 'w') as f:
     json.dump(result, f)

  # Delete database entry
  cursor = db.cursor()
  cursor.execute(f"delete from oc_filecache where fileid = {id}")
  db.commit()


def sql_query(query, db):
  db.reconnect()
  cursor = db.cursor()
  cursor.execute(query)
  return {id[0] for id in cursor.fetchall()}


s3_files = {int(object.key.split(":")[-1]) for object in mybucket.objects.all()}
"""
s3_objects = {obj.key: (obj.size, obj.e_tag, obj.last_modified)
              for obj in mybucket.objects.all()}
"""

# List previews (id, names, and parent image id) attached to a full image that doesn't exist anymore
# From https://github.com/charleypaulus/nextcloudtools/blob/main/delete-old-previews.sh
previews = sql_query("select tP.fileid, tP.name, tP.fullImgId from (select fileid, name, convert(substring_index(substring_index(path, '/', -2), '/', 1), UNSIGNED) as fullImgId from oc_filecache where path like '%/preview/%.jpg') as tP left join (select fileid from oc_filecache) as tA on tP.fullImgId = tA.fileid where tA.fileid is NULL order by tP.fullImgId", mydb)

## TODO: look into oc_files_versions and oc_files_trashbin too

files_n_folders = sql_query("select fileid from oc_filecache", mydb)
just_files = sql_query("select fileid from oc_filecache where mimetype > 2", mydb)
empty_folders = sql_query("select fileid from oc_filecache where mimetype = 2 and size = 0", mydb)

# S3 object has no DB entry: files deleted in DB but not in S3
s3_orphans = s3_files.difference(files_n_folders)

# DB entry has no S3 object: reserved thumbnails that were not generated or aborted uploads
db_orphans = just_files.difference(s3_files)

# Empty folder (as read in DB) has an S3 object ??? Bits were created by magic. AWS will charge real money though.
empty_not_empty = empty_folders.intersection(s3_files)

print(f"Detected {len(s3_files)} \tfiles from S3 bucket")
print(f"Detected {len(files_n_folders)} \tentries (files + folders) from oc_filecache")
print(f"Detected {len(just_files)} \tfiles from oc_filecache")
print("")
print(f"Detected {len(empty_folders)} \tempty folders from oc_files")
print(f"Detected {len(empty_not_empty)} \tempty folders from oc_files that have an S3 ID match")
print("")
print(f"Detected {len(previews)} \torphaned file previews from oc_filecache [no original file]")
print(f"Detected {len(s3_orphans)} \torphaned IDs from S3 [no match in oc_filecache]")
print(f"Detected {len(db_orphans)} \torphaned file IDs from DB [no match in S3]")

print("------------------------------------------------------------------")

if str.lower(input("Do you wish to cleanup ? Type `y` to proceed: ")) == "y":

  # Remove file previews of files that don't exist anymore
  for orphan in previews:
    fileid = orphan[0]
    backup_and_delete_db(fileid, mydb)
    backup_and_delete_s3(fileid, mybucket)

  # Remove empty directories having a matching S3 object
  for fileid in empty_not_empty:
    # Mostly .git/objects/ and appdata_xxxx/previews/
    backup_and_delete_db(fileid, mydb)
    backup_and_delete_s3(fileid, mybucket)

  # Remove empty directories not having an S3 object, aka the rest
  # No backup here, too many objects.
  if len(empty_folders) < 200000:
    mydb.reconnect()
    mycursor = mydb.cursor()
    mycursor.execute("delete from oc_filecache where mimetype = 2 and size = 0")
    mydb.commit()
  else:
    print("Run `delete from oc_filecache where mimetype = 2 and size = 0` directly on your MySQL server to remove empty directories. There are too many elements for us to try here remotely.")

  # Remove orphan S3 elements
  for fileid in s3_orphans:
    backup_and_delete_s3(fileid, mybucket)

  # Remove orphan DB elements
  for fileid in db_orphans:
    backup_and_delete_db(fileid, mydb)

  print("All done.")

else:
  print("Aborted.")
