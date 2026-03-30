import Image from "next/image";
import Link from "next/link";

export default function BrandLogo() {
  return (
    <Link href="/library" className="brand brand--logo" aria-label="ClauseIQ home">
      <Image
        src="/clauseiq-logo.png"
        alt="ClauseIQ"
        width={1024}
        height={682}
        className="brandLogoImg"
        priority
        sizes="(max-width: 320px) 75vw, 260px"
      />
    </Link>
  );
}
