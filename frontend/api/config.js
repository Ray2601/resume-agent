export default function handler(request, response) {
  response.status(200).json({ apiBaseUrl: process.env.API_BASE_URL || "http://127.0.0.1:8000" });
}
