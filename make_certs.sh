#!/bin/sh
# Create a private CA plus one controller and one driver certificate for mutual TLS.
# Usage: ./make_certs.sh OUTPUT_DIR DRIVER_HOST_OR_IP
# Keep ca.key private; copy only ca.crt and the matching cert/key to each machine.
set -eu
[ $# -eq 2 ] || { echo "Usage: $0 OUTPUT_DIR DRIVER_HOST_OR_IP" >&2; exit 1; }
out=$1; host=$2
[ ! -e "$out" ] || { echo "Refusing to overwrite $out" >&2; exit 1; }
umask 077; mkdir -p "$out"; cd "$out"
case "$host" in *[!0-9.]*) san="DNS:$host" ;; *) san="IP:$host" ;; esac
openssl req -x509 -newkey rsa:3072 -nodes -keyout ca.key -out ca.crt -days 365 -subj "/CN=dlt-ca" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" -addext "subjectKeyIdentifier=hash" 2>/dev/null
mk() { # name subjectAltName extendedKeyUsage
  openssl req -newkey rsa:3072 -nodes -keyout "$1.key" -out "$1.csr" -subj "/CN=$1" 2>/dev/null
  printf 'subjectAltName=%s\nextendedKeyUsage=%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nauthorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n' "$2" "$3" > "$1.ext"
  openssl x509 -req -in "$1.csr" -CA ca.crt -CAkey ca.key -CAcreateserial -out "$1.crt" -days 90 -extfile "$1.ext" 2>/dev/null
  rm -f "$1.csr" "$1.ext"
}
mk driver "$san" serverAuth,clientAuth
mk controller "DNS:controller" clientAuth
echo "Wrote ca.crt, driver.crt/key, controller.crt/key to $out"
