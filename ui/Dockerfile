# E1 placeholder: nginx serves a static page and forwards /api to the API.
# Task U1 replaces the static page with the React build.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY placeholder/ /usr/share/nginx/html/
