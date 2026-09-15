#!/bin/bash

fileDirectory="classes"

while IFS="," read -r proj pkg src agt mwt
do
  echo "Class: $src"
  echo ""

  sourceDir="MUT/$proj/$pkg/$src.java"
  agtDir="AGT/$proj/$pkg/$agt.java"
  mwtDir="MWT/$proj/$pkg/$mwt.java"

  python ./send_java_file_to_api.py --api-url http://localhost:800$1/graphql --agt_file_path $agtDir --mwt_file_path $mwtDir

  echo "Start"
  sleep 30  # wait for 3 seconds
  echo "30 seconds later..."

done < <(tail -n +2 $fileDirectory.csv)
