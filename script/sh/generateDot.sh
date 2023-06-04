#!/bin/bash

SOOT_JAR_PATH=$1
ANDROID_JARS_PATH=$2
APK_FILE=$3
SOOT_OUT_DIR=$4

java -cp "$SOOT_JAR_PATH" soot.tools.CFGViewer  \
 --graph=CompleteBlockGraph \
 -d "$SOOT_OUT_DIR" \
 -android-jars "$ANDROID_JARS_PATH" \
 -allow-phantom-refs \
 -src-prec apk \
 -ire \
 -f J \
 -process-dir "$APK_FILE"
