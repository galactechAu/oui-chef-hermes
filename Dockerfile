FROM alpine:3.21 AS whisper-builder
RUN apk add --no-cache build-base cmake git linux-headers
RUN git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git /opt/whisper.cpp
WORKDIR /opt/whisper.cpp
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON \
    && cmake --build build --target whisper-cli -j2

FROM python:3.13-alpine
RUN apk add --no-cache tesseract-ocr tesseract-ocr-data-eng font-dejavu yt-dlp ffmpeg curl libstdc++
COPY --from=whisper-builder /opt/whisper.cpp/build/bin/whisper-cli /usr/local/bin/whisper-cli
RUN mkdir -p /models \
    && curl -fsSL --retry 3 https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin -o /models/ggml-tiny.en.bin
WORKDIR /app
COPY app.py allergies.py core.py store.py generation.py recipe_page.py recipe_importer.py realtime.py ./
COPY static ./static
EXPOSE 8094
ENV PORT=8094
CMD ["python3", "app.py"]
