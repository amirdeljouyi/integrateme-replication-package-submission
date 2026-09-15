FROM eclipse-temurin:17-jdk

ARG MAVEN_VERSION=3.9.6
ARG GRADLE_VERSION=8.7

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl unzip python3 python3-pip ca-certificates bash \
 && rm -rf /var/lib/apt/lists/*

# Maven
RUN curl -fsSL https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz \
  | tar -xz -C /opt \
 && ln -s /opt/apache-maven-${MAVEN_VERSION}/bin/mvn /usr/local/bin/mvn

# Gradle
RUN curl -fsSL https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip -o /tmp/gradle.zip \
 && unzip -q /tmp/gradle.zip -d /opt \
 && ln -s /opt/gradle-${GRADLE_VERSION}/bin/gradle /usr/local/bin/gradle \
 && rm -f /tmp/gradle.zip

WORKDIR /work