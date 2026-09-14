#!/bin/sh
# Сборка Pari Collector без Gradle: aapt2 -> javac -> d8 -> zip -> apksigner
set -e
ROOT=/public/pari-apk
BLD=$ROOT/build
AAPT2_LD=/system/bin/linker64
AAPT2=/public/ocl-build/data/data/com.termux/files/usr/bin/aapt2
TUSRLIB=/public/ocl-build/data/data/com.termux/files/usr/lib
AJAR=/public/ocl-build/sdk/platforms/android-34/android.jar
D8=/public/ocl-build/d8.jar
SIGNER=/public/ocl-build/apksigner.jar

rm -rf $BLD
mkdir -p $BLD/gen $BLD/classes $BLD/dex

echo "== 1. aapt2 link"
LD_LIBRARY_PATH=$TUSRLIB $AAPT2_LD $AAPT2 link -o $BLD/unsigned.apk -I $AJAR \
  --manifest $ROOT/AndroidManifest.xml --min-sdk-version 28 --target-sdk-version 28 \
  --java $BLD/gen $ROOT/res/values/strings.xml 2>/dev/null || \
LD_LIBRARY_PATH=$TUSRLIB $AAPT2_LD $AAPT2 link -o $BLD/unsigned.apk -I $AJAR \
  --manifest $ROOT/AndroidManifest.xml --min-sdk-version 28 --target-sdk-version 28 \
  --java $BLD/gen

echo "== 2. javac"
find $ROOT/java $BLD/gen -name '*.java' > $BLD/srcs.txt
javac --release 11 -cp $AJAR -d $BLD/classes @$BLD/srcs.txt

echo "== 3. d8"
java -cp $D8 com.android.tools.r8.D8 --lib $AJAR --min-api 28 --output $BLD/dex \
  $(find $BLD/classes -name '*.class')

echo "== 4. apk"
python3 - "$BLD/unsigned.apk" "$BLD/dex/classes.dex" "$BLD/pari-unsigned.apk" <<'PYEOF'
import sys, zipfile
src, dex, out = sys.argv[1], sys.argv[2], sys.argv[3]
zin = zipfile.ZipFile(src, 'r')
zout = zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED)
for n in zin.namelist():
    if n == 'classes.dex':
        continue
    zout.writestr(zin.getinfo(n), zin.read(n))
zin.close()
zout.write(dex, 'classes.dex')
zout.close()
print('assembled')
PYEOF

echo "== 5. ключ"
if [ ! -f $ROOT/debug.keystore ]; then
  keytool -genkeypair -keystore $ROOT/debug.keystore -alias pari -keyalg RSA -keysize 2048 \
    -validity 10000 -storepass android -keypass android -dname "CN=PariCollector"
fi

echo "== 6. подпись"
java -jar $SIGNER sign --ks $ROOT/debug.keystore --ks-pass pass:android --ks-key-alias pari \
  --key-pass pass:android --out $ROOT/pari-collector.apk $BLD/pari-unsigned.apk
ls -la $ROOT/pari-collector.apk
echo BUILD-OK
