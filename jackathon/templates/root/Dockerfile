# Stage 1: build the React frontend
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install --ignore-scripts --no-audit --no-fund --loglevel=error
COPY frontend/ ./
RUN npm run build

# Stage 2: run the API and serve the built frontend from one container
FROM python:3.12-slim
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH" PORT=7860
WORKDIR /home/user/app
COPY --chown=user backend/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
COPY --chown=user backend/ .
COPY --chown=user --from=web /web/dist ./static
EXPOSE 7860
CMD ["sh", "-c", "uvicorn serve:app --host 0.0.0.0 --port ${PORT}"]
