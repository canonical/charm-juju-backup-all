#!/bin/bash
charm=$(grep -E "^name:" metadata.yaml | awk '{print $2}')
echo "renaming ${charm}_*.charm to ${charm}.charm"
echo -n "pwd: "
pwd
ls -al
echo "Removing previous charm if it exists"
if [[ -e "${charm}.charm" ]];
then
    rm "${charm}.charm"
fi
echo "Renaming charm here."
mv ${charm}_*.charm ${charm}.charm
