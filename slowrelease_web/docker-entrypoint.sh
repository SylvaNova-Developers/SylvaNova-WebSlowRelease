#!/bin/sh
set -eu

mkdir -p "$(dirname "${SLOWRELEASE_DB}")" "${SLOWRELEASE_CUSTOM_WORLDS:-/data/custom_worlds}"

# Archipelago loads apworlds from <local_path>/custom_worlds when the app dir is writable.
# Point that folder at the persistent volume.
app_custom="/app/custom_worlds"
data_custom="${SLOWRELEASE_CUSTOM_WORLDS:-/data/custom_worlds}"
if [ ! -e "$app_custom" ]; then
	ln -s "$data_custom" "$app_custom"
elif [ -d "$app_custom" ] && [ ! -L "$app_custom" ]; then
	# Prefer volume contents; merge any baked-in files once.
	cp -an "$app_custom"/. "$data_custom"/ 2>/dev/null || true
	rm -rf "$app_custom"
	ln -s "$data_custom" "$app_custom"
fi

exec "$@"
