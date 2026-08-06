/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emit a minimal, self-contained server bundle (.next/standalone) so the
  // production Docker image stays small and light on RAM — important on the
  // shared 4 GB VM. See DEPLOYMENT.md.
  output: "standalone",
};

export default nextConfig;
