ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM $BUILD_FROM

# numpy and OpenCV for the effects come from Alpine's own packages: pip has no
# OpenCV build for Alpine and would try to compile it for hours
RUN apk add --no-cache python3 py3-pip py3-numpy py3-opencv

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY . .
RUN chmod a+x /app/run.sh

CMD [ "/app/run.sh" ]
