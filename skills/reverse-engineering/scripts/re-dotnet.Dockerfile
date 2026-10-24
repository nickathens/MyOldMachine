# .NET decompiler for Unity / managed assemblies: ILSpy's command-line tool
# (ilspycmd) turns a compiled .NET assembly (Unity's Assembly-CSharp.dll and the
# like) back into readable C#. Runs ONLY through sandbox.py (no network,
# read-only root, no capabilities, non-root, memory capped), so an untrusted
# assembly is only ever read in isolation. The SDK is pinned by digest and
# ilspycmd by version; the SDK also lets a C# test assembly be compiled here, in
# the sandbox, for the proof. sandbox.py derives the image tag from this file.
FROM mcr.microsoft.com/dotnet/sdk@sha256:d9ad72f6b3eb5d61adb158db510ae1c3098c12c92d23ab8da833418275f1c652

ARG ILSPYCMD_VERSION=9.1.0.7988

ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_ROLL_FORWARD=LatestMajor \
    PATH=$PATH:/opt/ilspy

RUN set -eu; \
    dotnet tool install ilspycmd --version "$ILSPYCMD_VERSION" --tool-path /opt/ilspy; \
    /opt/ilspy/ilspycmd --version
