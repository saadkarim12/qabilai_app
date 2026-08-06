import { redirect } from "next/navigation";

// Root simply forwards to the HR area; the (protected) layout guard sends
// unauthenticated users to /login.
const Home = () => {
  redirect("/dashboard");
};

export default Home;
