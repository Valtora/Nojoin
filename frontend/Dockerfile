FROM node:20-alpine

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm install

# Copy source
COPY . .

# Expose the configured port
ENV PORT=14141
EXPOSE 14141

# Start in development mode (matches dev.ps1 behavior)
CMD ["npm", "run", "dev"]
