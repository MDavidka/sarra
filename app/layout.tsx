import React from 'react';
import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';
import Navbar from '@/components/Navbar';
import Footer from '@/components/Footer';
import StateTicker from '@/components/StateTicker';

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-sans',
});

export const metadata: Metadata = {
  title: 'AetherNode — Next-Gen Game Server Orchestration',
  description: 'Enterprise-grade game server hosting engineered for extreme performance, ultra-low latency, and maximum uptime. Deploy your server in under 55 seconds.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} bg-background text-foreground scroll-smooth`}>
      <body className="font-sans antialiased min-h-screen flex flex-col selection:bg-primary/30 selection:text-primary">
        {/* Background Radial Glow */}
        <div className="fixed inset-0 radial-glow pointer-events-none z-0" />
        <div className="fixed inset-0 cyber-grid pointer-events-none z-0 opacity-40" />

        {/* State Ticker for Background Instance Simulation */}
        <StateTicker />

        {/* Navigation */}
        <Navbar />

        {/* Main Content Area */}
        <main className="flex-grow pt-24 relative z-10">
          {children}
        </main>

        {/* Global Footer */}
        <Footer />
      </body>
    </html>
  );
}
