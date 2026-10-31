import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import "@/features/ui-layout/classic/classic.css";
import "@/features/ui-layout/modern/modern.css";
import ThemeScript from "@/components/ThemeScript";
import ToastViewport from "@/components/common/ToastViewport";
import MotionProvider from "@/components/common/MotionProvider";
import SettingsReturnTracker from "@/components/settings/SettingsReturnTracker";
import { WorkspaceNavigation } from "@/components/workspaces/WorkspaceNavigation";
import { AppShellProvider } from "@/context/AppShellContext";
import { I18nClientBridge } from "@/i18n/I18nClientBridge";

// Geist matches the public site (deeptutor.info) and stays crisp at the
// small UI sizes the composer/toolbars use, unlike the rounder Jakarta.
// The fonts are vendored under app/fonts so production builds never depend
// on fonts.googleapis.com being reachable.
const fontSans = localFont({
  src: "./fonts/geist-latin.woff2",
  weight: "100 900",
  display: "swap",
  variable: "--font-sans",
});

const fontSerif = localFont({
  src: "./fonts/lora-latin.woff2",
  weight: "400 700",
  display: "swap",
  variable: "--font-serif",
});

export const viewport: Viewport = { interactiveWidget: "resizes-content" };

export const metadata: Metadata = {
  title: "DeepTutor",
  description: "Agent-native intelligent learning companion",
  icons: {
    icon: [
      { url: "/brand/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/brand/favicon-32x32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: "/brand/apple-touch-icon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      data-scroll-behavior="smooth"
      className={`${fontSans.variable} ${fontSerif.variable}`}
    >
      <head>
        <ThemeScript />
      </head>
      <body
        className="font-sans bg-[var(--background)] text-[var(--foreground)]"
        suppressHydrationWarning
      >
        <AppShellProvider>
          <SettingsReturnTracker />
          <WorkspaceNavigation />
          <MotionProvider>
            <I18nClientBridge>{children}</I18nClientBridge>
          </MotionProvider>
          <ToastViewport />
        </AppShellProvider>
      </body>
    </html>
  );
}
