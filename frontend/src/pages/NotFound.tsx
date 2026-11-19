import { Link } from "react-router-dom";
import { Card, Empty } from "../components/ui";

export default function NotFound() {
  return <Card><Empty icon="search" title="Page not found" text="This page doesn't exist." action={<Link to="/" className="btn btn-primary">Go to Overview</Link>} /></Card>;
}
