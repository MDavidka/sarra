import { NextRequest, NextResponse } from "next/server";
import { sessionCookie, verifySession } from "../../../lib/access";

export async function GET(request: NextRequest) {
  return NextResponse.json({ authenticated: verifySession(request.cookies.get(sessionCookie)?.value) });
}
