#!/bin/sh
# Regenerate the Python and TypeScript clients from openapi/openapi.json.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
python3 "$ROOT/scripts/export_openapi.py"
VERSION=7.13.0
JAR="$ROOT/.openapi-generator/openapi-generator-cli-$VERSION.jar"
mkdir -p "$ROOT/.openapi-generator"
if [ ! -f "$JAR" ]; then
  curl -fsSL -o "$JAR" "https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/${VERSION}/openapi-generator-cli-${VERSION}.jar"
fi
rm -rf "$ROOT/sdk/python/generated" "$ROOT/sdk/typescript/generated"
java -jar "$JAR" generate \
  -i "$ROOT/openapi/openapi.json" \
  -g python \
  -o "$ROOT/sdk/python/generated" \
  --additional-properties=packageName=pik_client,projectName=pik-client,packageVersion=0.1.0,library=urllib3,hideGenerationTimestamp=true \
  --global-property apiTests=false,modelTests=false,apiDocs=false,modelDocs=false
java -jar "$JAR" generate \
  -i "$ROOT/openapi/openapi.json" \
  -g typescript-fetch \
  -o "$ROOT/sdk/typescript/generated" \
  --additional-properties=npmName=@partner-integration-kit/client,supportsES6=true,typescriptThreePlus=true,hideGenerationTimestamp=true
python3 - "$ROOT" << 'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1]) / "sdk" / "typescript" / "generated" / "src"
for path in root.rglob("*.ts"):
    text = path.read_text(encoding="utf-8")
    updated = text.replace("            ...value,\n", "")
    if updated != text:
        path.write_text(updated, encoding="utf-8")
PY
rm -rf "$ROOT/sdk/python/generated/.github" \
  "$ROOT/sdk/python/generated/.travis.yml" \
  "$ROOT/sdk/python/generated/.gitlab-ci.yml" \
  "$ROOT/sdk/python/generated/git_push.sh"
echo "generated sdk/python/generated and sdk/typescript/generated"
