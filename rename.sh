charm=$(grep -E "^name:" metadata.yaml | awk '{print $2}')
echo "renaming ${charm}_*.charm to ${charm}_series.charm"
echo -n "pwd: "
pwd
ls -al
echo "Removing previous charms if they exist"
if [[ -e "${charm}.charm" ]];
then
    rm "${charm}.charm"
fi
if [[ -e "${charm}_22.04.charm" ]];
then
    rm "${charm}_22.04.charm"
fi
if [[ -e "${charm}_20.04.charm" ]];
then
    rm "${charm}_20.04.charm"
fi

echo "Renaming charms here."
mv ${charm}_*22.04*.charm ${charm}_22.04.charm
mv ${charm}_*20.04*.charm ${charm}_20.04.charm