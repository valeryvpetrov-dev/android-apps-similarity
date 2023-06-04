#!/bin/bash

SOOT_JAR_PATH="/Users/va.petrov/Android/GitHub/soot/art/soot-4.4.1-jar-with-dependencies.jar"
ANDROID_JARS_PATH="/Users/va.petrov/Library/Android/sdk/platforms/"

APK_FILE=$1
SOOT_OUT_DIR=$2

java -cp $SOOT_JAR_PATH soot.Main  \
 -d $SOOT_OUT_DIR \
 -android-jars $ANDROID_JARS_PATH \
 -allow-phantom-refs \
 -src-prec apk \
 -ire \
 -f J \
 -process-dir $APK_FILE
