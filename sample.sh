#!/bin/bash
# sample.sh - Demonstrates all patterns that checker.py detects.
# Intentionally has no set -e or set -u to trigger warnings.

# --- Redirect operators ---
echo "hello" > out.txt
echo "world" >> out.txt
some_command &> combined.log
some_command &>> combined_append.log
some_command 2> error.log
some_command 2>> error_append.log
exec 3> persistent.log
echo "via fd" >&3

# --- touch / mkdir ---
touch logs/app.log
touch /tmp/lock.pid
mkdir -p build/output

# --- cp / mv / ln ---
cp src.sh /tmp/dest.sh
mv tmp.sh final.sh
ln -s /usr/bin/python3 ./python

# --- dd ---
dd if=/dev/zero of=disk.img bs=1M count=10

# --- tee (in a pipeline) ---
some_command | tee output.log
some_command | tee -a append.log

# --- sponge ---
some_command | sponge result.txt

# --- truncate ---
truncate -s 0 empty_file.txt

# --- mkfifo ---
mkfifo /tmp/my_pipe

# --- mktemp ---
TMPFILE=$(mktemp)

# --- curl / wget ---
curl -o download.tar.gz https://example.com/file.tar.gz
wget -O fetched.html https://example.com/

# --- sed in-place ---
sed -i 's/foo/bar/' config.txt

# --- openssl ---
openssl req -new -x509 -out cert.pem -keyout key.pem

# --- gpg ---
gpg -o encrypted.gpg -c secret.txt

# --- awk with internal redirect ---
awk '{print > "report.csv"}' data.txt
awk '{print >> "append.csv"}' data.txt

# --- variable in path ---
OUTPUT_DIR=/tmp/results
echo "data" > ${OUTPUT_DIR}/output.txt

# --- Use of LATE_VAR before it is assigned (warning: use before assign) ---
echo $LATE_VAR
LATE_VAR="defined later"

# --- Unquoted variable (warning) ---
cp $SRC $DEST
