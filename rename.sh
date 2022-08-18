#!/bin/bash
charm=$(grep -E "^name:" metadata.yaml | awk '{print $2}')
echo "renaming ${charm}_*.charm to ${charm}_series.charm"
echo -n "pwd: "
pwd
ls -al
echo "Removing previous charm if it exists"
if [[ -e "${charm}.charm" ]];
then
    rm "${charm}.charm"
fi
# Note(sudeephb): As two different charm files are built, 
# these are named according to the series they run on.
echo "Renaming charms here."
mv ${charm}_ubuntu-20.04-amd64_ubuntu-18.04-amd64.charm ${charm}_focal_bionic.charm
mv ${charm}_ubuntu-22.04-amd64.charm ${charm}_jammy.charm
