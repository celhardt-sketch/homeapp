# Stage 1: build the React frontend
FROM node:22-slim AS frontend
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig*.json ./
COPY public ./public
COPY src ./src
RUN npm run build

# Stage 2: FastAPI serves the API and the built frontend
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /web/dist ./static
RUN mkdir -p /data
EXPOSE 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
