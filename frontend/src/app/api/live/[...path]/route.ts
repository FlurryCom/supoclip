import { NextResponse } from "next/server";

import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

// Authenticated proxy for /live/* backend routes (followed Twitch channels).
const SAFE_PATH_SEGMENT = /^[A-Za-z0-9_-]+$/;

async function proxyLiveRequest(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { path } = await params;
  if (path.length === 0 || !path.every((segment) => SAFE_PATH_SEGMENT.test(segment))) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }

  const body = request.method === "GET" ? undefined : await request.text();
  // Query filters (e.g. trending game/language) pass through; the backend validates them.
  const search = new URL(request.url).search;
  const upstream = await fetchBackend(`/live/${path.join("/")}${search}`, {
    method: request.method,
    userId: session.user.id,
    extraHeaders: body ? { "Content-Type": "application/json" } : {},
    body: body || undefined,
    cache: "no-store",
  });
  return createTextProxyResponse(upstream);
}

export const GET = proxyLiveRequest;
export const POST = proxyLiveRequest;
export const PATCH = proxyLiveRequest;
export const DELETE = proxyLiveRequest;
