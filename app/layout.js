import { Inter, Plus_Jakarta_Sans, Geist_Mono } from "next/font/google";
import "./globals.css";
import Providers from "./providers";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});
const jakarta = Plus_Jakarta_Sans({
  variable: "--font-jakarta",
  subsets: ["latin"],
  display: "swap",
});
const geistMono = Geist_Mono({ variable: "--font-mono", subsets: ["latin"] });

export const metadata = {
  title: "Qabil.ai — HR",
  description: "AI-driven hiring automation",
};

const RootLayout = ({ children }) => (
  <html lang="en" className={`${inter.variable} ${jakarta.variable} ${geistMono.variable}`}>
    <body>
      <Providers>{children}</Providers>
    </body>
  </html>
);

export default RootLayout;
