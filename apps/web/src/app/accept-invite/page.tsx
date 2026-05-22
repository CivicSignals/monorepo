// Accept-invite page — /accept-invite?token=<raw-token> (B6).
//
// Server-component shell. The token arrives via the URL query param embedded
// in the invite email. The client island handles the two cases:
//   1. User is signed in → accept immediately.
//   2. User is not signed in → show "sign in or sign up to accept" CTA, then
//      after login redirect back to this page with the token preserved.
import type { Metadata } from "next";
import { AcceptInvitePage } from "./accept-invite-page";

export const metadata: Metadata = {
  title: "Accept invitation | CivicSignals",
  description: "Accept your invitation to join a CivicSignals workspace.",
};

export default function AcceptInvite() {
  return <AcceptInvitePage />;
}
