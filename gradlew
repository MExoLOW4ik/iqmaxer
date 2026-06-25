#!/bin/bash
# Gradle wrapper for Android build
DIR="$(cd "$(dirname "$0")" && pwd)"
GRADLE_VERSION="8.2"

# Download gradle if needed
if [ ! -f "$DIR/gradle/wrapper/gradle-wrapper.jar" ]; then
    echo "Downloading Gradle wrapper..."
    curl -sL "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" -o /tmp/gradle-bin.zip
    unzip -q /tmp/gradle-bin.zip -d /tmp/
    /tmp/gradle-${GRADLE_VERSION}/bin/gradle wrapper --project-dir "$DIR"
fi

exec /tmp/gradle-${GRADLE_VERSION}/bin/gradle "$@"
