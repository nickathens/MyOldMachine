import Swiper from 'swiper';
import { Navigation, Pagination, Autoplay } from 'swiper/modules';

new Swiper('.swiper', { modules: [Navigation, Pagination, Autoplay], loop: true, autoplay: { delay: 3000 } });
