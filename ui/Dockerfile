# Build the React app with Node, then serve the static files with nginx.
# nginx also forwards /api to the API (nginx.conf), so the browser talks to
# one origin and the API needs no CORS setup.

FROM node:24-alpine AS build
WORKDIR /ui
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /ui/dist /usr/share/nginx/html
