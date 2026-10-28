ARG BASE=itni-gui-base:local
FROM ${BASE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libegl1 libopengl0 libdbus-1-3 libxkbcommon0 fontconfig && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --only-binary=:all: PySide6==6.8.3 Pillow==11.3.0 && python -m pip check
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 && rm -rf /var/lib/apt/lists/*
ENV QT_QPA_PLATFORM=offscreen
RUN python -c "from PySide6.QtWidgets import QApplication; from PIL import Image; app=QApplication([]); print('GUI_IMAGE_RUNTIME_READY')"
USER 65534:65534
