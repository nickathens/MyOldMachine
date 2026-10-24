import EmblaCarousel from 'embla-carousel';

const embla = EmblaCarousel(document.querySelector('.embla'), { loop: true });
embla.on('select', () => {});
